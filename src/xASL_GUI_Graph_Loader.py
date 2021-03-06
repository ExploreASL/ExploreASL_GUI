from PySide2.QtWidgets import *
from PySide2.QtCore import Signal, Slot
from src.xASL_GUI_HelperFuncs_WidgetFuncs import robust_qmsg
import pandas as pd
from pathlib import Path
import numpy as np
import re


class xASL_GUI_Data_Loader(QWidget):
    """
    Class specifically dedicated for loading in the ExploreASL data from its Stats directory as well as loading in any
    ancillary data the user may provide
    """
    signal_dtype_was_changed = Signal(str, str)

    def __init__(self, parent):
        super(xASL_GUI_Data_Loader, self).__init__(parent=parent)
        self.parent_cw = parent
        self.loaded_wide_data = pd.DataFrame()
        self.loaded_long_data = pd.DataFrame()

        self.long_data_orig = pd.DataFrame()  # This will be the target of dtype alteration
        self.long_data_to_subset = pd.DataFrame()  # This will be the target of subsetting
        self.long_data = pd.DataFrame()  # This will be the target of plotting
        self.atlas_guide = {
            "MNI": "MNI_structural",
            "OASIS": "Mindboggle_OASIS",
            "Harvard-Oxford Cortical": "HOcort",
            "Harvard-Oxford Subcortical": "HOsub",
            "Hammers": "Hammers"
        }
        self.dtype_guide = {
            "SUBJECT": "object",
            "LongitudinalTimePoint": "category",
            "SubjectNList": "category",
            "Site": "category",
            "AcquisitionTime": "float64",
            "GM_vol": "float64",
            "WM_vol": "float64",
            "CSF_vol": "float64",
            "GM_ICVRatio": "float64",
            "GMWM_ICVRatio": "float64"
        }

    def load_exploreasl_data(self):
        # Cautionary measures
        from src.xASL_GUI_Plotting import xASL_Plotting
        self.parent_cw: xASL_Plotting
        stats_dir = Path(self.parent_cw.le_analysis_dir.text()) / "Population" / "Stats"
        if any([not stats_dir.exists(), not stats_dir.is_dir(), len(list(stats_dir.glob("*.tsv"))) == 0]):
            robust_qmsg(self.parent_cw, title=self.parent_cw.plot_errs["BadStudyDir"][0],
                        body=self.parent_cw.plot_errs["BadStudyDir"][1])
            return
        print("Loading in Data")
        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # First Section - Load in the ExploreASL Stats directory data
        atlas = self.atlas_guide[self.parent_cw.cmb_atlas_selection.currentText()]
        pvc = {"With PVC": "PVC2", "Without PVC": "PVC0"}[self.parent_cw.cmb_pvc_selection.currentText()]
        stat = {"Mean": "mean", "Median": "median",
                "Coefficient of Variation": "CoV"}[self.parent_cw.cmb_stats_selection.currentText()]

        # Clearing of appropriate widgets to accomodate new data
        self.parent_cw.lst_varview.clear()
        # Extract each as a dataframe and merge them
        pat_gm = f'{stat}_*_TotalGM*{pvc}.tsv'
        pat_wm = f'{stat}_*_DeepWM*{pvc}.tsv'
        pat_atlas = f'{stat}_*_{atlas}*{pvc}.tsv'
        dfs = []
        for pattern in [pat_gm, pat_wm, pat_atlas]:
            try:
                file = next(stats_dir.glob(pattern))
            except StopIteration:
                continue
            df = pd.read_csv(file, sep='\t')
            df.drop(0, axis=0, inplace=True)  # First row is unnecessary
            df = df.loc[:, [col for col in df.columns if "Unnamed" not in col]]
            dfs.append(df)
        if len(dfs) == 0:
            robust_qmsg(self.parent_cw, title="No Relevant Dataframes Found",
                        body="Could not locate any of the indicated atlas/pvc/stat .tsv files in the Stats directory "
                             "of this study. Has the user run the Population Module? If not, please run that module "
                             "before re-attempting.")
            return
        df: pd.DataFrame = pd.concat(dfs, axis=1)
        df = df.T.drop_duplicates().T

        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # Second Section - Fix the ExploreASL native data dtypes
        for col in df.columns:
            if col in self.dtype_guide.keys():
                df[col] = df[col].astype(self.dtype_guide[col])
            else:
                df[col] = df[col].astype("float64")
        self.loaded_wide_data = df
        self.backup_data = df.copy()

        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # Third Section - If there is any ancillary data specified, load it in
        meta_path = Path(self.parent_cw.le_metadata.text())
        if all([meta_path.exists(), meta_path.is_file(), meta_path.suffix in [".tsv", ".csv", ".xlsx"]]):
            result = self.load_ancillary_data(df)
            if result is not None:
                self.loaded_wide_data = result
            # If the merging failed, default to just using the ExploreASL datasets. In a future update, add some
            # sort of user feedback that this went wrong
            else:
                self.loaded_wide_data = self.backup_data

        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # Fourth Section - Convert the wide format data into a long format
        vars_to_keep_constant = [col for col in self.loaded_wide_data.columns if not any([col.endswith("_B"),
                                                                                          col.endswith("_L"),
                                                                                          col.endswith("_R")])]
        vars_to_melt = [col for col in self.loaded_wide_data.columns if col not in vars_to_keep_constant]
        self.loaded_long_data = self.loaded_wide_data.melt(id_vars=vars_to_keep_constant,
                                                           value_vars=vars_to_melt,
                                                           var_name="Atlas Location",
                                                           value_name="CBF")
        self.loaded_long_data["CBF"] = self.loaded_long_data["CBF"].astype("float64")
        atlas_location = self.loaded_long_data.pop("Atlas Location")
        atlas_loc_df: pd.DataFrame = atlas_location.str.extract("(.*)_(B|L|R)", expand=True)
        atlas_loc_df.rename(columns={0: "Anatomical Area", 1: "Side of the Brain"}, inplace=True)
        atlas_loc_df["Side of the Brain"] = atlas_loc_df["Side of the Brain"].apply(lambda x: {"B": "Bilateral",
                                                                                               "R": "Right",
                                                                                               "L": "Left"}[x])
        atlas_loc_df = atlas_loc_df.astype("category")
        self.loaded_long_data: pd.DataFrame = pd.concat([self.loaded_long_data, atlas_loc_df], axis=1)
        self.loaded_long_data = self.loaded_long_data.infer_objects()
        self.current_dtypes = self.loaded_long_data.dtypes
        self.current_dtypes = {col: str(str_name) for col, str_name in
                               zip(self.current_dtypes.index, self.current_dtypes.values)}
        self.parent_cw.lst_varview.addItems(self.loaded_long_data.columns.tolist())

        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # Fifth Section - Subset the data accordingly if the criteria is set
        self.parent_cw.subsetter.update_subsetable_fields_on_load(self.loaded_long_data)
        self.loaded_long_data: pd.DataFrame = self.parent_cw.subsetter.subset_data_on_load(self.loaded_long_data)

        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # Sixth Section - Housekeeping and Finishing touches
        # Alter this when Section 5 is completed; long_data is the "good copy" of the data that will be plotted
        self.long_data_orig = self.loaded_long_data.copy()  # THIS IS THE TARGET OF SUBSETTING
        self.long_data = self.loaded_long_data.copy()  # THIS IS OVERWRITTEN BY SUBSETTING THE ORIGINAL

        # Allow to dtype indicator to be aware of the newly loaded data if a legitimate covariates file was provided
        if all([meta_path.exists(), meta_path.is_file(), meta_path.suffix in [".tsv", ".csv", ".xlsx"]]):
            self.parent_cw.dtype_indicator.update_known_covariates(self.long_data)
            self.parent_cw.btn_indicate_dtype.setEnabled(True)
            for cmb in self.parent_cw.dtype_indicator.covariate_cols.values():
                cmb.activate()
                cmb.signal_sendupdateddtype.connect(self.update_datatype)
            print("Connected dtype indicator to subsetter")

        self.parent_cw.cmb_figuretypeselection.setEnabled(True)  # Data is loaded; figure selection settings enabled
        self.parent_cw.btn_subset_data.setEnabled(True)  # Data is loaded; subsetting is allowed

        # In case any of this was done again (data was already loaded once before), we must account for what may
        # have already been plotted or set; everything must be cleared. This should be as easy as setting the
        # figureselection to the first index, as plots & settings can only exist if its current index is non-zero,
        # and setting it to zero has the benefit of clearing everything else already
        if self.parent_cw.cmb_figuretypeselection.currentIndex() != 0:
            self.parent_cw.cmb_figuretypeselection.setCurrentIndex(0)
        print(f"DATAFRAME SHAPE UPON LOADING: {self.long_data.shape}")

    @Slot(str, str)
    def update_datatype(self, colname: str, newtype: str):
        df = self.long_data.copy()
        print(f"update_datatype received a signal to update the datatype of column {colname} to dtype: {newtype}")
        if len(self.long_data[colname].unique()) > 12 and newtype == "categorical":
            choice = QMessageBox().warning(self.parent_cw,
                                           "Confirm intended conversion",
                                           "You are converting a numerical into a categorical with more than 12 levels "
                                           "resulting from the conversion. This may cause instabilities when plotting. "
                                           "Proceed?",
                                           QMessageBox.Yes, QMessageBox.No)
            if choice == QMessageBox.Yes:
                vals = df[colname].values.astype(np.str)
                self.long_data[colname] = vals

                self.signal_dtype_was_changed.emit(colname, newtype)
            else:
                idx = self.parent_cw.dtype_indicator.covariate_cols[colname].findText("numerical")
                self.parent_cw.dtype_indicator.covariate_cols[colname].setCurrentIndex(idx)

        else:
            if newtype == "categorical":
                vals = df[colname].values.astype(np.str)
                self.long_data[colname] = vals
            elif newtype == "numerical":
                try:
                    vals = df[colname].values.astype(np.float)
                    self.long_data[colname] = vals
                # If attempting to convert to numerical from a categorical that isn't numbers, refuse the change
                except ValueError:
                    QMessageBox().warning(self.parent_cw, self.parent_cw.plot_errs["ImpossibleDtype"][0],
                                          self.parent_cw.plot_errs["ImpossibleDtype"][1], QMessageBox.Ok)

                    idx = self.parent_cw.dtype_indicator.covariate_cols[colname].findText("categorical")
                    self.parent_cw.dtype_indicator.covariate_cols[colname].setCurrentIndex(idx)

            # Memory cleanup
            del vals, df

            self.signal_dtype_was_changed.emit(colname, newtype)

    def load_ancillary_data(self, exasl_df):
        # Load in the other dataframe, with flexibility for filetype
        meta_file = Path(self.parent_cw.le_metadata.text())
        if meta_file.suffix == '.tsv':
            demo_df = pd.read_csv(meta_file, sep='\t')
        elif meta_file.suffix == '.csv':
            demo_df = pd.read_csv(meta_file)
        elif meta_file.suffix == '.xlsx':
            demo_df = pd.read_excel(meta_file)
        else:
            print("An unsupported filetype was given")
            QMessageBox().warning(self.parent_cw, self.parent_cw.plot_errs["BadMetaDataFile"][0],
                                  self.parent_cw.plot_errs["BadMetaDataFile"][1], QMessageBox.Ok)
            return None
        # Abort if the pertinent "SUBJECT" column is not in the read columns. In a future update, add support for user
        # specification of which column to interpret as the SUBJECT column
        if "SUBJECT" not in demo_df.columns:
            return None
        merged = pd.merge(left=demo_df, right=exasl_df, how='inner', on='SUBJECT', sort=True)
        if len(merged) == 0:
            return None

        # Perform a few dtype changes regarding typical categorical variables that may be in the covariates dataframe
        regex = re.compile(r"(sex|hand|site|\bscanner\b|education|employ|ethinicity|race|culture|gender|.*status\b|"
                           r".*type\b|.*disease\b)")
        colname: str
        for colname in merged.columns:
            if regex.search(colname.lower()):
                if str(merged[colname].dtype) not in ["object", "category"]:
                    print(f'Detected that the metadata column {colname} is a typical candidate for being '
                          f'misinterpreted as numerical. Setting as a categorical variable')
                    old_data = merged.pop(colname)
                    new_data = old_data.astype(np.str)
                    merged[colname] = new_data.astype("category")
                    del old_data, new_data

        sub_in_merge, sub_in_demo, sub_in_exasl = (set(merged["SUBJECT"].tolist()),
                                                   set(demo_df["SUBJECT"].tolist()),
                                                   set(exasl_df["SUBJECT"].tolist()))
        diff_in_demo = sub_in_demo.difference(sub_in_merge)
        diff_in_exasl = sub_in_exasl.difference(sub_in_merge)
        if any([len(diff_in_demo) > 0, len(diff_in_exasl) > 0]):
            QMessageBox().information(self.parent_cw,
                                      "Merge successful, but differences were found:\n",
                                      f"You provided a file with {len(sub_in_demo)} subjects.\n"
                                      f"ExploreASL's output had {len(sub_in_exasl)} subjects.\n"
                                      f"During the merge {len(diff_in_demo)} subjects present in the file "
                                      f"had to be excluded.\n"
                                      f"During the merge {len(diff_in_exasl)} subjects present in ExploreASL's output "
                                      f"had to be excluded",
                                      QMessageBox.Ok)
        return merged
