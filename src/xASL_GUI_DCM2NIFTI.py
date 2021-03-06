import numpy as np
import shutil
import subprocess
import nibabel as nib
import pydicom
from pydicom.errors import InvalidDicomError
from pydicom.multival import MultiValue
import json
import pandas as pd
from more_itertools import peekable, sort_together
import struct
import logging
from ast import literal_eval
from nilearn import image
from platform import system
from pathlib import Path
from typing import Union, List, Tuple
from datetime import datetime
import re

pd.set_option("display.width", 600)
pd.set_option("display.max_columns", 15)


def get_dicom_directories(config: dict) -> List[Tuple[Path]]:
    """
    Convenience function for globbing the dicom directories from the config file
    :param config: the configuration file that specifies the directory structure
    :return: dcm_firs: the list of filepaths to directories containing the dicom files
    """
    raw_dir = Path(config["RawDir"])
    n_levels_total: int = len(config["Directory Structure"])
    n_levels_subject: int = config["Directory Structure"].index("Subject") + 1
    delimiter: str = "\\" if system() == "Windows" else "/"

    subject_dirs = []
    dicom_dir: Path
    for subject_dir in raw_dir.glob(delimiter.join(["*"] * n_levels_subject)):
        dcms_per_subject = []
        for dicom_dir in subject_dir.glob(delimiter.join(["*"] * (n_levels_total - n_levels_subject))):
            hit = set(config["Scan Aliases"].values()).intersection(dicom_dir.parts)
            if len(hit) > 0:
                dcms_per_subject.append(dicom_dir)
        if len(dcms_per_subject) > 0:
            subject_dirs.append(tuple(dcms_per_subject))

    return subject_dirs


def get_value(subset, remaining_tags: List[Tuple[int]], default=None):
    for hex_pair in remaining_tags:
        in_subset = hex_pair in subset
        if any([not in_subset]):
            return default
        if not hasattr(subset[hex_pair], "value"):
            return default

        item = subset[hex_pair].value
        if isinstance(item, pydicom.DataElement):
            # Logic: no remaining tags can remain. If this is the case, an index error will be raised and the item can
            # be returned. Otherwise, there are remaining tags and this must be a false hit (i.e. float where there
            # should be a Sequence object).
            try:
                if len(remaining_tags[1:]) == 0:
                    return item.value
                else:
                    return default
            except IndexError:
                return item.value

        elif isinstance(item, pydicom.Sequence) and len(item) > 0:
            return get_value(subset=item[0], remaining_tags=remaining_tags[1:], default=default)
        else:
            # Logic: no remaining tags can remain. If this is the case, an index error will be raised and the item can
            # be returned. Otherwise, there are remaining tags and this must be a false hit (i.e. float where there
            # should be a Sequence object).
            try:
                if len(remaining_tags[1:]) == 0:
                    return item
                else:
                    return default
            except IndexError:
                return item


def get_dicom_value(data: pydicom.Dataset, tags: List[List[tuple]], default=None, for_byte_array=None):
    """
    Convenience function for retrieving the value of a dicom tag. Otherwise, returns the indicated default.

    :param data: the dicom data as a Pydicom Dataset object
    :param tags: a list of lists of tuples. Each 1st-level inner list describes an entire pathway to get
    to a value. Each 2nd-level inner list contains length-2 tuples that are each steps to getting to the
    desired value.
    :param default: the default value to return if nothing can be found
    :param for_byte_array: a byte string to use with regex in the event that the given tags result in a bytearray
    such that the expected string will be extracted
    :return: value: the first valid value associated with the tag
    """
    detected_values = []
    for tag_set in tags:
        detected_values.append(get_value(subset=data, remaining_tags=tag_set, default=default))

    # Additional for loop for types
    while default in detected_values:
        detected_values.remove(default)
    types = [type(value) for value in detected_values]

    if str in types and float in types:
        idx = types.index(float)
        return detected_values[idx]

    if str in types and int in types:
        idx = types.index(int)
        return detected_values[idx]

    for value in detected_values:
        if value is not None:
            if isinstance(value, str):
                if value.isnumeric():
                    return float(value)
                elif value.startswith("b'") and value.endswith("'"):
                    try:
                        return struct.unpack('f', literal_eval(value))[0]
                    except struct.error:
                        return float(literal_eval(value)[0])
                else:
                    return value
            elif isinstance(value, bytes):
                try:
                    return (struct.unpack("f", value))[0]
                except struct.error:
                    try:
                        return float(value.decode())
                    except UnicodeDecodeError:
                        # Rare scenario. A byte array is returned. Unable to parse this at the current time.
                        if not for_byte_array:
                            return default
                        try:
                            match = re.search(for_byte_array, value)
                            if not match:
                                return default
                            return match.group(1).decode()

                        except Exception as e:
                            print(f"Encountered EXCEPTION: {e}")
                            return default

            return value

    return default


def create_import_summary(import_summaries: list, config: dict):
    """
    Given a list of individual summaries of each subject/visit/scan, this function will bring all those givens
    together into a single dataframe for easy viewing
    :param import_summaries: a list of dicts, with each dict being the parameters of that subject-visit-scan
    :param config: the import configuration file generated by the GUI to help locate the analysis directory
    """
    analysis_dir = Path(config["RawDir"]).parent / "analysis"
    try:
        df = pd.concat([pd.Series(import_summary) for import_summary in import_summaries], axis=1, sort=True).T
    except ValueError as concat_error:
        print(concat_error)
        return

    df["dt"] = df["RepetitionTime"]
    appropriate_ordering = ['subject', 'visit', 'run', 'scan', 'dx', 'dy', 'dz', 'dt', 'nx', 'ny', 'nz', 'nt',
                            "RepetitionTime", "EchoTime", "NumberOfAverages", "RescaleSlope", "RescaleIntercept",
                            "MRScaleSlope", "AcquisitionTime",
                            "AcquisitionMatrix", "TotalReadoutTime", "EffectiveEchoSpacing"]
    df = df.reindex(columns=appropriate_ordering)
    df = df.sort_values(by=["scan", "subject", "visit", "run"]).reset_index(drop=True)
    print(df)
    now_str = datetime.now().strftime("%a-%b-%d-%Y %H-%M-%S")
    try:
        df.to_csv(analysis_dir / f"Import_Dataframe_{now_str}.tsv", sep='\t', index=False, na_rep='n/a')
    except PermissionError:
        df.to_csv(analysis_dir / f"Import_Dataframe_{now_str}_copy.tsv", sep='\t', index=False, na_rep='n/a')


def bids_m0_followup(analysis_dir: Path):
    """
    In a BIDS import, this function will run through the imported dataset and adjust any BIDS-standard fields that
    should be present in the m0scan.json sidecar, such as "IntendedFor"
    :param analysis_dir: the absolute path to the analysis directory
    """
    m0_jsons = peekable(analysis_dir.rglob("*_m0scan.json"))
    if not m0_jsons:
        print("bids_m0_followup could not find any _m0scan.json files")
        return
    for m0_json in m0_jsons:
        asl_json = m0_json.with_name("_asl.json")
        asl_nifti = m0_json.with_name("_asl.nii")

        # Ensure that the asl json sidecar and nifti images actually exist adjacent to the m0scan.json
        if asl_json.exists() and asl_nifti.exists():
            # BIDS standard: the "IntendedFor" filepath must be relative to the subject (exclusive)
            # and contain forward slashes
            truncated_asl_nifti = str(asl_nifti).replace(str(analysis_dir), "").replace("\\", "/")
            by_parts = truncated_asl_nifti.split(sep='/')
            truncated_asl_nifti = "/".join(by_parts[2:])

            with open(m0_json) as m0_json_reader:
                m0_parms = json.load(m0_json_reader)
            m0_parms["IntendedFor"] = truncated_asl_nifti
            with open(m0_json, 'w') as m0_json_writer:
                json.dump(m0_parms, m0_json_writer, indent=3)


class DCM2NIFTI_Converter:
    def __init__(self, config: dict, name: str, logger: logging.Logger, b_legacy: bool = True):
        """
        Class to perform DCM2NIFTI Conversion & Logging
        """
        self.config: dict = config
        self.b_legacy: bool = b_legacy
        self.path_sourcedir: Path = Path(config["RawDir"])
        self.delimiter: str = "\\" if system() == "Windows" else "/"

        # Prepare the logging credentials
        self.logger: logging.Logger = logger
        self.handler = logging.FileHandler(filename=self.path_sourcedir / f"tmpImport_{name}.log", mode="w")
        self.handler.setFormatter(logging.Formatter(fmt="%(asctime)s - %(name)s - %(levelname)s\n%(message)s"))
        self.handler.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)

        # Other attributes
        # Prep a translator that maps scan aliases to standard names (i.e. "ASL4D", "M0", etc.)
        self.scan_translator: dict = {value: key for key, value in self.config["Scan Aliases"].items()}

        self.tags_dict: dict = {
            "AcquisitionMatrix": {"tags": [[(0x0018, 0x1310)],
                                           [(0x5200, 0x9230), (0x0018, 0x1310)],
                                           [(0x5200, 0x9230), (0x2005, 0x140F), (0x0018, 0x1310)]],
                                  "default": None},
            "SoftwareVersions": {"tags": [[(0x0018, 0x1020)]],
                                 "default": None},
            "RescaleSlope": {
                "tags": [
                    [(0x0028, 0x1053)], [(0x2005, 0x110A)], [(0x2005, 0x140A)],
                    [(0x5200, 0x9230), (0x0028, 0x9145), (0x0028, 0x1053)]
                ],
                "default": 1},
            "RescaleIntercept": {"tags": [(0x0028, 0x1052)],
                                 "default": 0},
            "MRScaleSlope": {
                "tags": [
                    [(0x2005, 0x120E)], [(0x2005, 0x110E)], [(0x2005, 0x100E)],
                    [(0x5200, 0x2930), (0x2005, 0x140F), (0x2005, 0x100E)],
                    [(0x5200, 0x2930), (0x2005, 0x140F), (0x2005, 0x120E)],
                    [(0x5200, 0x9230), (0x2005, 0x140F), (0x2005, 0x100E)],
                    [(0x5200, 0x9230), (0x2005, 0x140F), (0x2005, 0x120E)]
                ],
                "default": 1},
            "RealWorldValueSlope": {"tags": [[(0x0040, 0x9096), (0x0040, 0x9225)]],
                                    "default": None},
            "NumberOfSlices": {"tags": [[(0x0054, 0x0081)]],
                               "default": None},
            "AcquisitionTime": {"tags": [[(0x0008, 0x0032)]],
                                "default": 0},
            "ScanOptions": {"tags": [[(0x0018, 0x0022)]],
                            "default": ""},
            "SpectrallySelectedSuppression": {"tags": [
                [(0x0018, 0x9025)],
                [(0x2005, 0x110F), (0x0018, 0x9025)],
                [(0x2005, 0x120F), (0x0018, 0x9025)],
                [(0x2005, 0x140F), (0x0018, 0x9025)],
                [(0x2005, 0x140F)],
                [(0x2005, 0x110F)]
            ],
                "default": None,
                "for_byte_array": b'\x18\x00%\x90\x04\x00\x00\x00(FAT|WATER|NONE|FAT_AND_WATER)'}
        }
        self.summary_data = {}
        self.logger.info(f"Initialized Logger for {name}")

    def process_dcm_dir(self, dcm_dir: Path):
        module_names = ["Getting File Structure Components", "Generating a TEMP Destination",
                        "Acquiring Additional DICOM Parms",
                        "DCM2NIIX Conversion", "NIFTI Cleanup", "Post-Processing JSON sidecar and NIFTI files"]
        funcs = [self.get_structure_components, self.get_tempdst_dirname, self.get_additional_dicom_parms,
                 self.run_dcm2niix, self.process_niftis_in_temp, self.update_final_json_and_nifti]

        self.summary_data.clear()
        start_str = f"START PROCESSING DICOM DIR {str(dcm_dir)}\n"
        self.logger.info("%" * len(start_str) + "\n" +
                         start_str +
                         "%" * len(start_str) + "\n")
        for func, desc in zip(funcs, module_names):
            self.logger.info(f"Beginning Module - {desc}")
            successfully_completed = func(dcm_dir)
            if not successfully_completed:
                return False, f"\nERROR_LISTING FOR DICOM DIRECTORY WITH GIVENS:\n\t" \
                              f"SUBECT: {self.subject}\n\t" \
                              f"VISIT: {self.visit}\n\t" \
                              f"RUN: {self.run}\n\t" \
                              f"SCAN: {self.scan}\n\t" \
                              f"ERROR at section {desc}"
            else:
                self.logger.info(f"Completed Module - {desc}\n")

        self.print_and_log(f"SUCCESSFUL IMPORT\n\n", msg_type="info")
        self.cleanup()
        return True, f"{str(dcm_dir)} was correctly converted from DICOM to NIFTI format"

    def print_and_log(self, msg: str, msg_type: str = "error"):
        if msg_type in {"info", "warning", "error"}:
            getattr(self.logger, msg_type)(msg)
        print(msg)

    def cleanup(self):
        # Remove the TEMP directory
        if self.path_tempdir.exists():
            shutil.rmtree(path=str(self.path_tempdir), ignore_errors=True)

    def get_structure_components(self, dcm_dir: Path):
        """
        Step 1: Determine the appropriate Subject, Visit, Run, and Scan names from the given path
        """
        self.subject, self.visit, self.run, self.scan = None, None, None, None
        for path_partname, dir_type in zip(reversed(dcm_dir.parts), reversed(self.config["Directory Structure"])):
            setattr(self, dir_type.lower(), path_partname)
        msg = f"The DICOM directory was determined to have the following givens:" \
              f"\n\tSubject: {self.subject}\n\tVisit: {self.visit}\n\tRun: {self.run}\n\tScan: {self.scan}"
        self.print_and_log(msg, msg_type="info")

        try:
            assert self.subject is not None
            assert self.scan is not None
        except AssertionError:
            self.logger.exception(f"The DICOM directory encountered an exception when retireving scan components")
            return False

        # Store the destination names as attributes; these will be used elsewhere
        self.subject_dst_name: str = self.subject
        self.visit_dst_name: Union[str, None] = self.visit
        self.scan_dst_name: str = self.scan_translator[self.scan]  # THIS IS ONE OF "ASL4D", "M0", "T1" or "FLAIR"
        self.run_dst_name: Union[str, None] = self.config["Ordered Run Aliases"][self.run] if self.run is not None \
            else None
        return True

    def get_tempdst_dirname(self, _):
        """
        Step 2: Determine the appropriate destination path for DCM2NIIX to act on
        """
        path_study_dir = self.path_sourcedir.parent / "analysis"
        # Non-BIDS FORMAT
        if self.b_legacy:
            subject_str = self.subject_dst_name
            visit_str = "" if self.visit_dst_name is None else f"_{self.visit_dst_name}"
            run_str = "ASL_1" if self.run_dst_name is None else self.run_dst_name
            if self.scan_dst_name not in {"T1", "T2", "FLAIR"}:
                self.path_tempdir = path_study_dir / f"{subject_str}{visit_str}" / run_str / "TEMP"
            else:
                self.path_tempdir = path_study_dir / f"{subject_str}{visit_str}" / "TEMP"
        # BIDS FORMAT
        else:
            # Get rid of illegal characters for subject
            subject_str = self.subject_dst_name.replace("-", "").replace("_", "")
            anat_or_perf = "anat" if self.scan_dst_name not in {"T1", "T2", "FLAIR"} else "perf"
            if self.visit_dst_name is None:
                self.path_tempdir = path_study_dir / f"sub-{subject_str}" / anat_or_perf / "TEMP"
            else:
                # Get rid of illegal characters for visit
                visit_str = self.visit_dst_name.replace("-", "").replace("_", "")
                self.path_tempdir = path_study_dir / f"sub-{subject_str}" / f"ses-{visit_str}" / anat_or_perf / "TEMP"

        self.path_tempdir.mkdir(parents=True, exist_ok=True)
        msg = f"The DICOM directory will have its DICOM files temporarily converted to NIFTI format and output to:\n" \
              f"{str(self.path_tempdir)}"
        self.print_and_log(msg, "info")
        return True

    def get_additional_dicom_parms(self, dcm_dir: Path):
        """
        Step 3: DCM2NIIX does not always retrieve the needed DICOM parameters, some must be retrieved
        """
        dcm_files = peekable(dcm_dir.glob("*"))
        if not dcm_files:
            self.print_and_log(f"The DICOM directory was empty!", msg_type="error")
            return False

        dcm_data = None
        for dcm_file in dcm_files:

            if dcm_file.name.startswith("XX"):
                continue
            try:
                dcm_data = pydicom.read_file(dcm_file)
            except (InvalidDicomError, PermissionError):
                continue
            except IsADirectoryError:
                self.print_and_log(f"Bad Folder Structure Provided! User probably forgot to indicate a DUMMY variable!",
                                   msg_type="error")
                return False
        if dcm_data is None:
            self.print_and_log(f"The DICOM directory did not contain any valid DICOM files which could be parsed",
                               msg_type="error")
            return False
        else:
            self.dcm_dataset: pydicom.Dataset = dcm_data

        manufacturer = get_dicom_value(data=dcm_data, tags=[[(0x0008, 0x0070)], [(0x0019, 0x0010)]], default=None)
        if manufacturer is None:
            self.print_and_log(f"The DICOM directory could not have its Manufacturer tag determined!!!", "error")
            return False
        if "SIEMENS" in manufacturer.upper():
            manufacturer = "Siemens"
        elif "PHILIPS" in manufacturer.upper():
            manufacturer = "Philips"
        elif "GE" in manufacturer.upper():
            manufacturer = "GE"
        else:
            self.print_and_log(f"The DICOM directory did not have a manufacturer of either Philips, Siemens, or GE!!!",
                               msg_type="error")
            return False
        try:
            if manufacturer != 'Philips':
                del self.tags_dict["RealWorldValueSlope"]
                del self.tags_dict["MRScaleSlope"]
        except KeyError:
            pass

        self.dcm_info = {}.fromkeys(self.tags_dict.keys())
        self.dcm_info["Manufacturer"] = manufacturer
        value: dict
        for key, value in self.tags_dict.items():
            result = get_dicom_value(data=dcm_data, tags=value["tags"], default=value["default"],
                                     for_byte_array=value.get("for_byte_array", None))
            if isinstance(result, MultiValue):
                result = list(result)
            # Additional processing for specific keys
            if key == "AcquisitionMatrix":
                if isinstance(result, str):
                    result = [int(number) for number in result.strip('[]').split(", ")]
                elif isinstance(result, list):
                    result = [int(number) for number in result]
                elif result is None:
                    backup = get_dicom_value(dcm_data, [[(0x5200, 0x9230), (0x0021, 0x10FE), (0x0021, 0x1058)]])
                    if backup is not None:
                        col, row = [int(x) for x in backup.split("*")]
                        result = [row, 0, 0, col]

            # Convert any lingering strings to float
            if key in ["NumberOfAverages", "RescaleIntercept", "RescaleSlope", "MRScaleSlope", "RealWorldValueSlope"]:
                if result is not None and not isinstance(result, list):
                    try:
                        result = float(result)
                    except ValueError:
                        pass

            self.dcm_info[key] = result
            # Final corrections for Philips scans in particular
            if manufacturer == "Philips":
                # First correction - disagreeing values between RescaleSlope and RealWorldValueSlope if they ended up
                # in the same dicom. Choose the small value of the two and set it for both
                if all([self.dcm_info["RescaleSlope"] is not None,
                        self.dcm_info["RealWorldValueSlope"] is not None,
                        self.dcm_info["RescaleSlope"] != 1,
                        self.dcm_info["RealWorldValueSlope"] != 1,
                        self.dcm_info["RescaleSlope"] != self.dcm_info["RealWorldValueSlope"]
                        ]):
                    self.dcm_info["RescaleSlope"] = min([self.dcm_info["RescaleSlope"],
                                                         self.dcm_info["RealWorldValueSlope"]])
                    self.dcm_info["RealWorldValueSlope"] = min([self.dcm_info["RescaleSlope"],
                                                                self.dcm_info["RealWorldValueSlope"]])

                # Second correction - just to ease things on the side of ExploreASL; if RescaleSlope could not be
                # determined while "RealWorldValueSlope" could be, copy over the latter's value for the former
                if all([self.dcm_info["RealWorldValueSlope"] is not None,
                        self.dcm_info["RealWorldValueSlope"] != 1,
                        self.dcm_info["RescaleSlope"] == 1]):
                    self.dcm_info["RescaleSlope"] = self.dcm_info["RealWorldValueSlope"]

        # remove the "RealWorldValueSlope" as it is no longer needed
        try:
            del self.dcm_info["RealWorldValueSlope"]
        except KeyError:
            pass
        msg = "\n".join(["The following DICOM Parameters were additionally extracted:"] +
                        [f"\t{k}: {v}" for k, v in self.dcm_info.items()])
        self.print_and_log(msg, msg_type="info")
        return True

    def run_dcm2niix(self, dcm_dir: Path):
        """
        Step 4: Run DCM2NIIX
        """
        # Prepare the necessary strings for the output files
        visit_str = "" if self.visit_dst_name is None else f"{self.visit_dst_name}"
        run_str = "_1" if self.run_dst_name is None else f"_{self.run_dst_name}"
        scan_str = f"_{self.scan_dst_name}"
        output_filename_format = f"{self.subject_dst_name}{visit_str}{scan_str}{run_str}_%s"
        msg = f"Prior to conversion by DCM2NIIX, the following are given:\n" \
              f"\tSubject: {self.subject_dst_name}\n\tVisit: {self.visit_dst_name}\n\tScan: {self.scan_dst_name}\n" \
              f"\tRun: {self.run_dst_name}\n\tOutputTEMPDir: {self.path_tempdir}"
        self.print_and_log(msg, msg_type="info")

        # Prepare the body of the main command
        header = "" if system() == "Windows" else "./"
        command = f"{header}dcm2niix -b y -z n -x n -t n -m n -s n -v n " \
                  f"-f {output_filename_format} -o {str(self.path_tempdir)} {str(dcm_dir)}"

        # Execute DCM2NIIX
        if system() == "Windows":
            result = subprocess.run(command.split(" "), creationflags=subprocess.CREATE_NO_WINDOW)
            stderr, return_code = result.stderr, result.returncode
        else:
            p = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
                                 text=True, shell=True)
            p.wait()
            stdout, stderr = p.communicate()
            return_code = p.returncode

        if return_code == 0:
            self.print_and_log(f"DCM2NIIX successfully converted files to NIFTI format!", msg_type="info")
            return True
        else:
            self.print_and_log(f"DCM2NIIX Did not exit gracefully!!!\nStd Err:\n{stderr}", msg_type="error")
            return False

    def process_niftis_in_temp(self, _):
        """
        Step 5: Clean up the mess that is present in the TEMP directory
        """
        ge_fix_flag, ge_json_file = False, None
        import_summary = dict.fromkeys(["subject", "visit", "scan", "filename",
                                        "dx", "dy", "dz", "nx", "ny", "nz", "nt"])
        jsons = peekable(self.path_tempdir.glob("*.json"))
        if not jsons:
            self.print_and_log(f"No JSON sidecars were present in the TEMP destination", msg_type="error")
            return False

        ###################################
        # PART 1 - ORGANIZING DCM2NII FILES
        ###################################
        self.print_and_log("Ascertaining the ordering of NIFTI and JSON files in TEMP directory", msg_type="info")
        # Must go over the jsons first to gain an understanding of the series
        json_data = {"SeriesNumber": {}, "AcquisitionTime": {}, "AcquisitionNumber": {}}
        for json_file in jsons:
            with open(json_file) as json_reader:
                sidecar_data: dict = json.load(json_reader)
                for parm in json_data.keys():
                    try:
                        json_data[parm][json_file.with_suffix(".nii")] = sidecar_data[parm]
                    # If AcquisitionTime isn't found in the JSON sidecars, there is usually TriggerDelayTime which can
                    # act as a surrogate key for organizing JSON sidecars & NIFTI files
                    except KeyError:
                        if parm == "AcquisitionTime":
                            json_data[parm][json_file.with_suffix(".nii")] = 0
                            if "TriggerDelayTime" in list(sidecar_data.keys()):
                                json_data[parm][json_file.with_suffix(".nii")] = sidecar_data["TriggerDelayTime"]

        # Philips case; same series and probably same acq number too; opt for acq_time as the differentiating factor
        if len(set(json_data["SeriesNumber"].values())) == 1 and len(set(json_data["AcquisitionTime"].values())) > 1:
            reorganized_data = {key: value for key, value in sorted(json_data["AcquisitionTime"].items(),
                                                                    key=lambda x: x[1])}
            reorganized_niftis = list(reorganized_data.keys())

        # Better Siemens scenario, usually has sequential series that increments
        elif len(set(json_data["SeriesNumber"].values())) > 1:
            reorganized_data = {key: value for key, value in sorted(json_data["SeriesNumber"].items(),
                                                                    key=lambda x: x[1])}
            reorganized_niftis = list(reorganized_data.keys())

        # Maybe the acquisition number is by chance different?
        elif len(set(json_data["AcquisitionNumber"].values())) > 1:
            reorganized_data = {key: value for key, value in sorted(json_data["AcquisitionNumber"].items(),
                                                                    key=lambda x: x[1])}
            reorganized_niftis = list(reorganized_data.keys())

        # Rare Siemens
        elif len(set(json_data["SeriesNumber"].values())) == 1 and len(set(json_data["AcquisitionTime"])) == 1:
            reorganized_data = {key: value for key, value in sorted(json_data["AcquisitionTime"].items(),
                                                                    key=lambda x: x[1])}
            reorganized_niftis = list(reorganized_data.keys())

        else:
            reorganized_data = {key: value for key, value in sorted(json_data["AcquisitionNumber"].items(),
                                                                    key=lambda x: x[1])}
            reorganized_niftis = list(set(reorganized_data.keys()))
            if len(reorganized_niftis) == 0:
                self.print_and_log("Could not ascertain an ordering to the JSON and NIFTI files in the TEMP directory."
                                   "\nAbandoning conversion rather than risking an incorrect concatenation.", "error")
                return False

        self.print_and_log("Successfully ascertained an ordering to the JSON and NIFTI files", msg_type="info")

        ########################################
        # PART 2 PROCESSING THE ORGANIZED NIFTIS
        ########################################
        self.print_and_log(f"Attempting to process NIFTI Files in the TEMP directory", msg_type="info")
        # Must process niftis differently depending on the scan and the number present after conversion
        # Scenario: ASL4D
        if len(reorganized_niftis) > 1 and self.scan_dst_name == "ASL4D":
            self.print_and_log(f"NIFTI Scenario: Multiple ASL NIFTIs needing to be concatenated", msg_type="info")
            initial_shape_history = []
            nii_objs = []

            # GE Fix, sometimes the vendors mix up the Perfusion vs M0 ordering; best to make sure each time
            if self.dcm_info["Manufacturer"] == "GE" and len(reorganized_niftis) == 2:
                self.print_and_log(f"GE Perfusion & M0 scenario: Attempting to ensure the ordering is correct", "info")
                is_perf, num_avrgs, json_files, nifti_files, data2write = [], [], [], [], None
                jsons = list(self.path_tempdir.glob("*.json"))
                try:
                    for jsonfile in jsons:
                        with open(jsonfile, "r") as ge_reader:
                            tmp_data: dict = json.load(ge_reader)
                            perf_check = int("PERFUSION" in list(map(str.upper, tmp_data["ImageType"])))
                            is_perf.append(perf_check)
                            if perf_check:
                                data2write = tmp_data.copy()
                            num_avrgs.append(tmp_data.get("NumberOfAverages", 1))
                            json_files.append(jsonfile)
                            nifti_files.append(jsonfile.with_suffix(".nii"))

                    # Sort iterables on the truth of them being Perfusion or not
                    if len(set(is_perf)) == 2 and data2write is not None:
                        iterable = [is_perf, num_avrgs, json_files, nifti_files]
                        is_perf, num_avrgs, json_files, reorganized_niftis = sort_together(iterable)
                        # Since the 2nd element must be True/1 (is a Perfusion) file, write to that json file
                        ge_json_file = json_files[1]
                        with open(ge_json_file, "w") as ge_writer:
                            data2write["NumberOfAverages"] = num_avrgs
                            json.dump(data2write, ge_writer, indent=3)
                        ge_fix_flag = True
                except KeyError:
                    self.print_and_log(f"GE Perfusion & M0 scenrio: could not retrieve the ImageType. Abandoning "
                                       f"attempt", msg_type="error")
                    pass

            for idx, nifti in enumerate(reorganized_niftis):
                nii_obj: nib.Nifti1Image = nib.load(str(nifti))

                # Must keep a history of incoming shapes to prevent incompatible later scans from ruining the concat
                if idx > 0 and nii_obj.shape not in initial_shape_history:
                    break
                initial_shape_history.append(nii_obj.shape)

                # dcm2niix error: imports a 4D NIFTI instead of a 3D one. Solution: must be split first and concatenated
                # with the others at a later step
                if len(nii_obj.shape) == 4:
                    volumes = nib.funcs.four_to_three(nii_obj)
                    for volume in volumes:
                        nii_objs.append(volume)

                # Otherwise, correct 3D import
                elif len(nii_obj.shape) == 3:

                    # dcm2niix error: imports a 3D mosaic. Solution: reformat as a 3D stack
                    if nii_obj.shape[2] == 1:
                        if idx == 0:
                            self.print_and_log("The NIFTI Files were determined to be incorrectly processed by DCM2NIX "
                                               ", resulting in a mosaic outcome. Converting mosaic to 3D volume",
                                               "warning")

                        # Get the acquisition matrix
                        acq_matrix = self.dcm_info["AcquisitionMatrix"]
                        if acq_matrix[0] == 0:
                            acq_rows = int(acq_matrix[1])
                            acq_cols = int(acq_matrix[2])
                        else:
                            acq_rows = int(acq_matrix[0])
                            acq_cols = int(acq_matrix[3])

                        nii_obj = self.fix_mosaic(mosaic_nifti=nii_obj, acq_dims=(acq_rows, acq_cols))
                    nii_objs.append(nii_obj)
                else:
                    self.print_and_log(f"An uncanny NIFTI set was encountered. A single NIFTI in this set had the "
                                       f"following shape: {nii_obj.shape}", msg_type="error")
                    return False

            final_nifti_obj = nib.funcs.concat_images(nii_objs)

        # Scenario: multiple M0; will take their mean as final
        elif len(reorganized_niftis) > 1 and self.scan_dst_name == "M0":
            self.print_and_log(f"NIFTI Scenario: Multiple M0 to be averaged", msg_type="info")
            nii_objs = [nib.load(nifti) for nifti in reorganized_niftis]
            final_nifti_obj = image.mean_img(nii_objs)
            # Must correct for bad headers under BIDS specification
            if not self.b_legacy and final_nifti_obj.ndim < 4:
                pixdim_copy = final_nifti_obj.header["pixdim"].copy()
                final_nifti_obj.header["dim"][0] = 4
                final_nifti_obj.header["pixdim"] = pixdim_copy
                final_nifti_obj = nib.Nifti1Image(np.expand_dims(image.get_data(final_nifti_obj), axis=-1),
                                                  final_nifti_obj.affine,
                                                  final_nifti_obj.header)

        # Scenario: single M0 or single ASL4D
        elif len(reorganized_niftis) == 1 and self.scan_dst_name in ["M0", "ASL4D"]:
            self.print_and_log(f"NIFTI Scenario: Single M0 or ASL scan (i.e. CBF or PWI Image)", msg_type="info")
            final_nifti_obj: nib.Nifti1Image = nib.load(reorganized_niftis[0])

            # Weird GE flavor which DCM2NIIX gets wrong (ends up as a 3D when it needs to be 4D)
            if all([self.dcm_info["Manufacturer"] == "GE", "EPI" in sidecar_data.get("ScanOptions", ""),
                    len(final_nifti_obj.shape) == 3
                    ]):
                n_temporal = get_dicom_value(self.dcm_dataset, [[(0x0020, 0x0105)]], default=None)
                n_images = get_dicom_value(self.dcm_dataset, [[(0x0020, 0x1002)]], default=None)
                if any([n_images is None, n_temporal is None]):
                    self.print_and_log("Could not parse GE 2D-EPI ")
                self.print_and_log("Weird GE 2D-EPI Scenario: DCM2NIIX Concatenated Incorrectly. Fixing Issue.")
                old_data = final_nifti_obj.get_fdata(dtype=np.float32)
                new_data = np.stack(tuple(reversed(np.dsplit(old_data, n_temporal))))
                new_data = np.transpose(new_data, (1, 2, 3, 0))
                final_nifti_obj: nib.Nifti1Image = image.new_img_like(final_nifti_obj, data=new_data,
                                                                      affine=final_nifti_obj.affine)

            # Must correct for bad headers under BIDS specification
            if not self.b_legacy and final_nifti_obj.ndim < 4:
                pixdim_copy = final_nifti_obj.header["pixdim"].copy()
                final_nifti_obj.header["dim"][0] = 4
                final_nifti_obj.header["pixdim"] = pixdim_copy
                final_nifti_obj = nib.Nifti1Image(np.expand_dims(image.get_data(final_nifti_obj), axis=-1),
                                                  final_nifti_obj.affine,
                                                  final_nifti_obj.header)

        # Scenario: one of the structural types
        elif len(reorganized_niftis) == 1 and self.scan_dst_name in ["T1", "T2", "FLAIR"]:
            self.print_and_log(f"NIFTI Scenario: Single Structural Scan", msg_type="info")
            final_nifti_obj = nib.load(str(reorganized_niftis[0]))

        # Scenario: multiple T1 acquisitions...take the mean
        elif len(reorganized_niftis) > 1 and self.scan_dst_name in ["T1", "T2", "FLAIR"]:
            self.print_and_log(f"NIFTI Scenario: Multiple T1 Scans. Taking their mean...", msg_type="info")
            nii_objs = [nib.load(str(nifti)) for nifti in reorganized_niftis]
            final_nifti_obj = image.mean_img(nii_objs)

        # Otherwise, something went wrong and the operation should stop
        else:
            msg = f"Error in clean_niftis_in_temp while attempting to process:\nsubject {self.subject_dst_name}" \
                  f"; visit {self.visit_dst_name}; run {self.run_dst_name}; scan {self.scan_dst_name}\n" \
                  f"Reorganized_niftis did not fit into any of the foreseen scenarios\n" \
                  f"Length of reorganized_niftis: {len(reorganized_niftis)}"
            self.print_and_log(msg, msg_type="error")
            return False

        self.print_and_log("Successfully created a single NIFTI file appropriate for analysis", msg_type="info")
        # Take the oppurtunity to get more givens for the import summary and add it to the summary_data attribute
        zooms = final_nifti_obj.header.get_zooms()
        shape = final_nifti_obj.shape
        import_summary["subject"] = self.subject_dst_name
        import_summary["visit"] = self.visit_dst_name
        import_summary["run"] = self.run_dst_name
        import_summary["scan"] = self.scan_dst_name
        import_summary["filename"] = self.scan_dst_name + ".nii"

        if len(zooms) >= 4:
            import_summary["dx"], import_summary["dy"], import_summary["dz"] = zooms[0:3]
        else:
            import_summary["dx"], import_summary["dy"], import_summary["dz"] = zooms

        if len(shape) == 4:
            (import_summary["nx"], import_summary["ny"],
             import_summary["nz"], import_summary["nt"]) = shape
        else:
            (import_summary["nx"], import_summary["ny"],
             import_summary["nz"], import_summary["nt"]) = shape[0], shape[1], shape[2], 1
        self.summary_data.update(import_summary)
        self.print_and_log("Successfully added shape and zoom information to the main data summary", msg_type="info")

        ################################
        # PART 3 NAMING AND MOVING FILES
        ################################

        # Get the destination filepaths
        self.print_and_log("Moving NIFTI and JSON files out of the TEMP directory", msg_type="info")
        run_str = "" if self.run_dst_name is None else f"run-{self.run_dst_name.replace('-', '').replace('_', '')}"
        visit_str = "" if self.visit_dst_name is None \
            else f"ses-{self.visit_dst_name.replace('-', '').replace('_', '')}_"
        if self.b_legacy:
            self.path_final_nifti = self.path_tempdir.parent / f"{self.scan_dst_name}.nii"
            self.path_final_json = self.path_tempdir.parent / f"{self.scan_dst_name}.json"
        else:
            scan_str = {"ASL4D": "asl", "M0": "m0scan", "T1": "T1w", "T2": "T2w", "FLAIR": "FLAIR"}[self.scan_dst_name]
            subject_str = self.subject_dst_name.replace("-", "").replace("_", "")
            basename_str = f"sub-{subject_str}_{visit_str}{run_str}{scan_str}"
            self.path_final_nifti = self.path_tempdir.parent / f"{basename_str}.nii"
            self.path_final_json = self.path_tempdir.parent / f"{basename_str}.json"
        self.print_and_log(f"Determined the final NIFTI and JSON filepaths to be as follows:\n"
                           f"\t NIFTI: {str(self.path_final_nifti)}\n"
                           f"\t JSON: {str(self.path_final_json)}", msg_type="info")

        # Perform the file move operations
        nib.save(final_nifti_obj, self.path_final_nifti)
        if ge_fix_flag and ge_json_file is not None:
            ge_json_file.replace(self.path_final_json)
        else:
            jsons = peekable(self.path_tempdir.glob("*json"))
            if not jsons:
                self.print_and_log(f"Error in clean_niftis_in_temp while attempting to rename remaining json files",
                                   msg_type="error")
                return False
            json_file = next(jsons)
            json_file.replace(self.path_final_json)

        return True

    @staticmethod
    def fix_mosaic(mosaic_nifti: nib.Nifti1Image, acq_dims: tuple):
        """
        Fixes incorrectly-processed NIFTIs by dcm2niix where they still remain mosaics due to a lack of
        NumberOfImagesInMosaic header. This function implements a hack to
        :param mosaic_nifti: the nifti image object that needs to be fixed. Should be of shape m x n x 1
        the sliding window algorithm
        :param acq_dims: the (row, col) acquisition dimensions for rows and columns from the AcquisitionMatrix DICOM
        field.
        Used to determine the appropriate kernel size to use for the sliding window algorithm
        :return: new_nifti; a 3D NIFTI that is no longer mosaic
        """
        acq_rows, acq_cols = acq_dims

        # Get the shape and array values of the mosaic (flatten the latter into a 2D array)
        img_shape = mosaic_nifti.shape
        # noinspection PyTypeChecker
        img_data = np.rot90(np.squeeze(mosaic_nifti.get_fdata()))

        # If this is a square, and the rows perfectly divides the mosaic
        if img_shape[0] == img_shape[1] and img_shape[0] % acq_rows == 0:
            nsplits_w, nsplits_h = img_shape[0] / acq_rows, img_shape[0] / acq_rows
            kernel_w, kernel_h = acq_rows, acq_rows
        # If this is a square, and the cols perfectly divides the mosaic
        elif img_shape[0] == img_shape[1] and img_shape[0] % acq_cols == 0:
            nsplits_w, nsplits_h = img_shape[0] / acq_cols, img_shape[0] / acq_cols
            kernel_w, kernel_h = acq_cols, acq_cols
        # If this is a rectangle
        elif all([img_shape[0] != img_shape[1],
                  img_shape[0] % acq_rows == 0,
                  img_shape[1] % acq_cols == 0
                  ]):
            nsplits_w, nsplits_h = img_shape[0] / acq_rows, img_shape[1] / acq_cols
            kernel_w, kernel_h = acq_rows, acq_cols
        else:
            return

        # Initialize the data that will house the split mosaic into slices
        new_img_data = np.zeros(shape=(kernel_w, kernel_h, int(nsplits_w * nsplits_h)))
        slice_num = 0

        # Sliding Window algorithm
        for ii in range(int(nsplits_w)):
            for jj in range(int(nsplits_h)):
                x_start, x_end = ii * kernel_w, (ii + 1) * kernel_w
                y_start, y_end = jj * kernel_h, (jj + 1) * kernel_h
                img_slice = img_data[x_start:x_end, y_start:y_end]

                # Disregard slices that are only zeros
                if np.nanmax(img_slice) == 0:
                    continue
                # Otherwise update the zeros array at the appropriate slice with the new values
                else:
                    new_img_data[:, :, slice_num] = img_slice
                    slice_num += 1

        # Filter off slices that had only zeros
        new_img_data = np.rot90(new_img_data[:, :, 0:slice_num], 3)
        new_nifti = image.new_img_like(mosaic_nifti, new_img_data, affine=mosaic_nifti.affine)
        return new_nifti

    def update_final_json_and_nifti(self, _):
        """
        Step 6 Add in missing data to the JSON sidecars and Account for the Philips NIFTI correction that may need to
        take place
        """
        if any([not self.path_final_json.exists(), not self.path_final_nifti.exists()]):
            msg = f"Could not find the remaining json sidecar or NIFTI file for updating json " \
                  f"sidecars or fixing NIFTI headers, respectively." \
                  f"\n\tJSON exists? {self.path_final_json.exists()}\n" \
                  f"\n\tNIFTI exists? {self.path_final_nifti.exists()}"
            self.print_and_log(msg, msg_type="error")
            return False

        with open(self.path_final_json) as json_sidecar_reader:
            json_sidecar_parms: dict = json.load(json_sidecar_reader)

        json_sidecar_parms.update({k: v for k, v in self.dcm_info.items() if v is not None})
        # First, rename certain elements
        for old_name, new_name in {"EstimatedEffectiveEchoSpacing": "EffectiveEchoSpacing",
                                   "EstimatedTotalReadoutTime": "TotalReadoutTime"}.items():
            if old_name in json_sidecar_parms.keys():
                json_sidecar_parms[new_name] = json_sidecar_parms.pop(old_name)

        # Next, must see if Philips-related fixes post-DCM2NIIX are necessary
        manufac = json_sidecar_parms.get("Manufacturer", None)
        if manufac == "Philips":
            # One possibility: Array values are Stored Values and must be corrected to Philips Floating Point
            if all(["PhilipsRescaleSlope" in json_sidecar_parms,
                    "PhilipsRescaleIntercept" in json_sidecar_parms,
                    "PhilipsScaleSlope" in json_sidecar_parms,
                    "PhilipsRWVSlope" not in json_sidecar_parms,
                    json_sidecar_parms.get("UsePhilipsFloatNotDisplayScaling", None) == 0,
                    ]):
                nifti_msg = f"NIFTI Additional Tweaks Scenario: Philips NIFTI featured Stored Values " \
                            f"that had to be converted to Philips Floating Point"
                nifti_img: nib.Nifti1Image = image.load_img(str(self.path_final_nifti))
                nifti_data: np.ndarray = image.get_data(nifti_img)
                RI = json_sidecar_parms["PhilipsRescaleIntercept"]
                RS = json_sidecar_parms["PhilipsRescaleSlope"]
                SS = json_sidecar_parms["PhilipsScaleSlope"]
                new_nifti_data = (nifti_data + (RI / RS)) / SS
                new_nifti = nib.Nifti1Image(dataobj=new_nifti_data, header=nifti_img.header, affine=nifti_img.affine)
                del nifti_img, nifti_data, RI, RS, SS, new_nifti_data
                new_nifti.to_filename(str(self.path_final_nifti))
                del new_nifti
                json_sidecar_parms["UsePhilipsFloatNotDisplayScaling"] = 1

            # Another possibility: Array values were incorrectly converted to Display Values rather than Philips
            # Floating Point
            elif all(["PhilipsRescaleSlope" in json_sidecar_parms,
                      "PhilipsRescaleIntercept" in json_sidecar_parms,
                      "PhilipsScaleSlope" in json_sidecar_parms,
                      "PhilipsRWVSlope" in json_sidecar_parms,
                      json_sidecar_parms.get("UsePhilipsFloatNotDisplayScaling", None) == 1,
                      ]):
                nifti_msg = f"NIFTI Additional Tweaks Scenario: Philips NIFTI featured arrays values " \
                            f"that were incorrectly converted to Display Values rather than Philips Floating Point."
                nifti_img: nib.Nifti1Image = image.load_img(str(self.path_final_nifti))
                nifti_data: np.ndarray = image.get_data(nifti_img)
                RS, SS = json_sidecar_parms["PhilipsRescaleSlope"], json_sidecar_parms["PhilipsScaleSlope"]
                new_nifti_data = nifti_data / (RS * SS)
                new_nifti = nib.Nifti1Image(dataobj=new_nifti_data, header=nifti_img.header, affine=nifti_img.affine)
                del nifti_img, nifti_data, RS, SS, new_nifti_data
                new_nifti.to_filename(str(self.path_final_nifti))
                del new_nifti

            else:
                nifti_msg = f"NIFTI Additional Tweaks Scenario: Philips NIFTI already had proper values."
            self.print_and_log(nifti_msg, msg_type="info")

        self.summary_data.update(json_sidecar_parms)
        with open(self.path_final_json, "w") as json_sidecar_writer:
            json.dump(json_sidecar_parms, json_sidecar_writer, indent=3)

        return True
