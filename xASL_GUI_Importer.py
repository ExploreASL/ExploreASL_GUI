from PySide2.QtWidgets import *
from PySide2.QtGui import *
from PySide2.QtCore import *
from xASL_GUI_HelperClasses import DandD_FileExplorer2LineEdit
from glob import glob
from tdda import rexpy
from pprint import pprint
import json
import os
import shutil
import re
import sys
import platform


# noinspection PyCallingNonCallable
class xASL_GUI_Importer(QMainWindow):
    def __init__(self, parent_win=None):
        # Parent window is fed into the constructor to allow for communication with parent window devices
        super().__init__(parent=parent_win)
        if parent_win is not None:
            self.config = self.parent().config
        else:
            with open("ExploreASL_GUI_masterconfig.json") as f:
                self.config = json.load(f)

        # Misc and Default Attributes
        self.labfont = QFont()
        self.labfont.setPointSize(16)
        self.rawdir = ''
        self.folderhierarchy = ['', '', '']
        self.tokenordering = ['', '', '']
        self.subject_regex = None
        self.session_regex = None
        self.scan_regex = None
        self.session_aliases = {}
        self.scan_aliases = dict.fromkeys(["ASL4D", "T1", "M0", "FLAIR", "WMH_SEGM"])

        # Window Size and initial visual setup
        self.setMinimumSize(540, 720)
        self.resize(540, 720)
        self.setWindowTitle("ExploreASL ASL2BIDS Importer")
        self.cw = QWidget(self)
        self.setCentralWidget(self.cw)
        self.mainlay = QVBoxLayout(self.cw)
        self.mainsplit = QSplitter(Qt.Vertical, self.cw)
        self.cw.setLayout(self.mainlay)
        self.mainlay.addWidget(self.mainsplit)

        self.Setup_UI_UserSpecifyDirStuct()
        self.Setup_UI_UserSpecifyScanAliases()
        self.Setup_UI_UserSpecifySessionAliases()

        self.btn_run_importer = QPushButton("Run ASL2BIDS", self.cw, clicked=self.run_importer)
        self.btn_run_importer.setFont(self.labfont)
        self.btn_run_importer.setFixedHeight(50)
        self.mainsplit.addWidget(self.btn_run_importer)

        self.mainsplit.setSizes([150, 250, 300, 50])

    def Setup_UI_UserSpecifyDirStuct(self):
        self.grp_dirstruct = QGroupBox("Specify Directory Structure", self.cw)
        self.grp_dirstruct.setMaximumHeight(225)
        self.vlay_dirstruct = QVBoxLayout(self.grp_dirstruct)

        # First specify the root directory
        self.formlay_rootdir = QFormLayout()
        self.hlay_rootdir = QHBoxLayout()
        self.le_rootdir = DandD_FileExplorer2LineEdit(self.grp_dirstruct)
        self.le_rootdir.setPlaceholderText("Drag and drop your study's raw directory here")
        self.le_rootdir.setReadOnly(True)
        self.le_rootdir.textChanged.connect(self.set_rootdir_variable)
        self.btn_rootdir = QPushButton("...", self.grp_dirstruct, clicked=self.set_import_root_directory)
        self.hlay_rootdir.addWidget(self.le_rootdir)
        self.hlay_rootdir.addWidget(self.btn_rootdir)
        self.formlay_rootdir.addRow("Raw Root Directory", self.hlay_rootdir)

        # Next specify the QLabels that can be dragged to have their text copied elsewhere
        self.hlay_placeholders = QHBoxLayout()
        self.lab_holdersub = DraggableLabel("Subject", self.grp_dirstruct)
        self.lab_holdersess = DraggableLabel("Session", self.grp_dirstruct)
        self.lab_holderscan = DraggableLabel("Scan", self.grp_dirstruct)
        for lab in [self.lab_holdersub, self.lab_holdersess, self.lab_holderscan]:
            self.hlay_placeholders.addWidget(lab)

        # Next specify the QLineEdits that will be receiving the dragged text
        self.hlay_receivers = QHBoxLayout()
        self.lab_rootlabel = QLabel("raw", self)
        self.lab_rootlabel.setFont(self.labfont)
        self.levels = {"Level1": DandD_Label2LineEdit(self, self.grp_dirstruct),
                       "Level2": DandD_Label2LineEdit(self, self.grp_dirstruct),
                       "Level3": DandD_Label2LineEdit(self, self.grp_dirstruct)}
        self.levels["Level1"].textChanged.connect(self.get_firstlevel_dirs)
        self.levels["Level2"].textChanged.connect(self.get_secondlevel_dirs)
        self.levels["Level3"].textChanged.connect(self.get_thirdlevel_dirs)
        for le in self.levels.values():
            le.textChanged.connect(self.update_sibling_awareness)

        self.hlay_receivers.addWidget(self.lab_rootlabel)
        if platform.system() == "Windows":
            separator = '\\'
        else:
            separator = '/'
        lab_sep = QLabel(separator, self.grp_dirstruct)
        lab_sep.setFont(self.labfont)
        self.hlay_receivers.addWidget(lab_sep)
        for ii, level in enumerate(self.levels.values()):
            level.setFont(self.labfont)
            self.hlay_receivers.addWidget(level)
            if ii < 2:
                lab_sep = QLabel(separator, self.grp_dirstruct)
                lab_sep.setFont(self.labfont)
                self.hlay_receivers.addWidget(lab_sep)

        self.btn_clear_receivers = QPushButton("Clear the fields", self.grp_dirstruct, clicked=self.clear_receivers)

        # Organize layouts
        self.vlay_dirstruct.addLayout(self.formlay_rootdir, 1)
        self.vlay_dirstruct.addLayout(self.hlay_placeholders, 2)
        self.vlay_dirstruct.addLayout(self.hlay_receivers, 2)
        self.vlay_dirstruct.addWidget(self.btn_clear_receivers, 2)

        self.mainsplit.addWidget(self.grp_dirstruct)

    def Setup_UI_UserSpecifyScanAliases(self):
        # Next specify the scan aliases
        self.grp_scanaliases = QGroupBox("Specify Scan Aliases", self.cw)
        self.cmb_scanaliases_dict = dict.fromkeys(["ASL4D", "T1", "M0", "FLAIR", "WMH_SEGM"])
        self.formlay_scanaliases = QFormLayout(self.grp_scanaliases)
        for description, scantype in zip(["ASL scan alias:\n(Mandatory)", "T1 scan alias:\n(Mandatory)",
                                          "M0 scan alias:\n(Optional)", "FLAIR scan alias:\n(Optional)",
                                          "WHM Segmentation image alias:\n(Optional)"],
                                         self.cmb_scanaliases_dict.keys()):
            cmb = QComboBox(self.grp_scanaliases)
            cmb.addItems(["Select an alias"])
            cmb.currentTextChanged.connect(self.update_scan_aliases)
            self.cmb_scanaliases_dict[scantype] = cmb
            self.formlay_scanaliases.addRow(description, cmb)

        self.mainsplit.addWidget(self.grp_scanaliases)

    def Setup_UI_UserSpecifySessionAliases(self):
        # Define the groupbox and its main layout
        self.grp_sessionaliases = QGroupBox("Specify Session Aliases and Ordering", self.cw)
        self.vlay_sessionaliases = QVBoxLayout(self.grp_sessionaliases)
        self.scroll_sessionaliases = QScrollArea(self.grp_sessionaliases)
        self.cont_sessionaliases = QWidget()
        self.scroll_sessionaliases.setWidget(self.cont_sessionaliases)
        self.scroll_sessionaliases.setWidgetResizable(True)

        # Arrange widgets and layouts
        self.le_sessionaliases_dict = dict()
        self.formlay_sessionaliases = QFormLayout(self.cont_sessionaliases)
        self.vlay_sessionaliases.addWidget(self.scroll_sessionaliases)
        self.mainsplit.addWidget(self.grp_sessionaliases)

        # for ii in range(10):
        #     self.formlay_sessionaliases.addRow(str(ii), QLabel(str(ii)))

    def clear_receivers(self):
        for le in self.levels.values():
            le.clear()

    # Purpose of this function is to set the directory of the root path lineedit based on the adjacent pushbutton
    @Slot()
    def set_import_root_directory(self):
        dir_path = QFileDialog.getExistingDirectory(QFileDialog(),
                                                    "Select the raw directory of your study",
                                                    self.parent().config["DefaultRootDir"],
                                                    QFileDialog.ShowDirsOnly)
        if os.path.exists(dir_path):
            self.le_rootdir.setText(dir_path)

    # Purpose of this function is to change the value of the rawdir attribute based on the current text
    @Slot()
    def set_rootdir_variable(self):
        self.rawdir = self.le_rootdir.text()

    # Checks if any of subjects, sessions, or scans needs resetting
    def check_if_reset_needed(self):
        used_directories = [le.text() for le in self.levels.values()]
        # If subjects is not in the currently-specified structure and the regex has been already set
        if "Subject" not in used_directories and self.subject_regex is not None:
            self.subject_regex = None

        # If sessions is not in the currently-specified structure and the regex has been already set
        if "Session" not in used_directories and self.session_regex is not None:
            self.session_regex = None
            self.session_aliases.clear()
            self.reset_session_alias_cmbs()  # This clears the sessionaliases dict and the widgets

        if "Scan" not in used_directories and self.scan_regex is not None:
            self.scan_regex = None
            self.scan_aliases.clear()
            self.reset_scan_alias_cmbs(basenames=[])

    # Purpose of this function is to return a list of the first level directories immediately succeeding raw
    def get_firstlevel_dirs(self, dir_type: str):
        """
        :param dir_type: whether this is a subject, session, or scan
        """
        # Avoid false runs
        if any([self.rawdir == '',  # the attribute cannot be blank
                not os.path.exists(self.rawdir),  # the attribute must be a path that exists
                os.path.basename(self.rawdir) != 'raw',  # avoid other directories
                ]):
            return

        # First check if a reset is needed anywhere
        self.check_if_reset_needed()

        # Then glob the full paths
        directories = [directory for directory in glob(os.path.join(self.rawdir, '*')) if os.path.isdir(directory)]

        # If the search is fruitless, do not proceed and immediately clear the lineedit the label was dropped into
        if len(directories) == 0:
            self.levels["Level1"].clear()
            return

        # Otherwise, proceed
        self.get_nlevel_dirs(dir_type=dir_type, directories=directories)

    def get_secondlevel_dirs(self, dir_type: str):
        """
        :param dir_type: whether this is a subject, session, or scan
        """
        # Avoid false runs
        if any([self.rawdir == '',  # the attribute cannot be blank
                not os.path.exists(self.rawdir),  # the attribute must be a path that exists
                os.path.basename(self.rawdir) != 'raw',  # avoid other directories
                ]):
            return

        # First check if a reset is needed anywhere
        self.check_if_reset_needed()

        # Then glob the full paths
        directories = [directory for directory in glob(os.path.join(self.rawdir, '*', "*")) if os.path.isdir(directory)]

        # If the search is fruitless, do not proceed and immediately clear the lineedit the label was dropped into
        if len(directories) == 0:
            self.levels["Level2"].clear()
            return

        # Otherwise, proceed
        self.get_nlevel_dirs(dir_type=dir_type, directories=directories)

    def get_thirdlevel_dirs(self, dir_type: str):
        """
        :param dir_type: whether this is a subject, session, or scan
        """
        # Avoid false runs
        if any([self.rawdir == '',  # the attribute cannot be blank
                not os.path.exists(self.rawdir),  # the attribute must be a path that exists
                os.path.basename(self.rawdir) != 'raw',  # avoid other directories
                ]):
            return

        # First check if a reset is needed anywhere
        self.check_if_reset_needed()

        # Then glob the full paths
        directories = [directory for directory in glob(os.path.join(self.rawdir, '*', "*", "*")) if
                       os.path.isdir(directory)]

        # If the search is fruitless, do not proceed and immediately clear the lineedit the label was dropped into
        if len(directories) == 0:
            self.levels["Level3"].clear()
            return

        # Otherwise, proceed
        self.get_nlevel_dirs(dir_type=dir_type, directories=directories)

    def get_nlevel_dirs(self, dir_type, directories):
        """
        :param dir_type: whether this is a subject, session, or scan
        :param directories: the list of directories produced from globbing a certain depth depending on which drag and
        drop QLabel was performed
        """
        basenames = set([os.path.basename(path) for path in directories])
        if dir_type == "Subject":
            self.subject_regex = self.inferregex(list(basenames))
            print(f"Subject regex: {self.subject_regex}")

        elif dir_type == "Session":
            self.session_regex = self.inferregex(list(basenames))
            print(f"Session regex: {self.session_regex}")
            self.update_session_aliases(basenames=list(basenames))

        elif dir_type == "Scan":
            self.scan_regex = self.inferregex(list(basenames))
            self.reset_scan_alias_cmbs(basenames=list(basenames))

        else:
            print("Error. This should never print")
            return

    # Purpose of this function is to reset all the comboboxes of the scans section and repopulate them with new options
    def reset_scan_alias_cmbs(self, basenames):
        for cmb in self.cmb_scanaliases_dict.values():
            cmb.clear()
            cmb.currentTextChanged.disconnect(self.update_scan_aliases)
            cmb.addItems(["Select an alias"] + basenames)
            cmb.currentTextChanged.connect(self.update_scan_aliases)

    # Convenience function for resetting all the lineedits for the session aliases
    def reset_session_alias_cmbs(self):
        for idx in range(self.formlay_sessionaliases.rowCount()):
            self.formlay_sessionaliases.removeRow(0)
        self.le_sessionaliases_dict.clear()

    # Purpose of this function is to update the global attribute for the scan aliases as the comboboxes are selected
    def update_scan_aliases(self):
        for key, value in self.cmb_scanaliases_dict.items():
            if value.currentText() != "Select an alias":
                self.scan_aliases[key] = value.currentText()
            else:
                self.scan_aliases[key] = None

    # Purpose of this function is to update the lineedits of the sessions section and repopulate
    def update_session_aliases(self, basenames):
        # If this is an update, remove the previous widgets and clear the dict
        if len(self.le_sessionaliases_dict) > 0:
            self.reset_session_alias_cmbs()

        # Generate the new dict, populate the format layout, and add the lineedits to the dict
        self.le_sessionaliases_dict = dict.fromkeys(basenames)

        for ii, key in enumerate(self.le_sessionaliases_dict):
            hlay = QHBoxLayout()
            cmb = QComboBox()
            nums_to_add = [str(num) for num in range(1, len(self.le_sessionaliases_dict)+1)]
            cmb.addItems(nums_to_add)
            cmb.setCurrentIndex(ii)
            le = QLineEdit()
            le.setPlaceholderText("(Optional) Specify the alias for this session")
            hlay.addWidget(le)
            hlay.addWidget(cmb)

            self.formlay_sessionaliases.addRow(key, hlay)
            self.le_sessionaliases_dict[key] = le

    # Convenience function for returning the regex string, provided a list of directories
    @staticmethod
    def inferregex(dirs):
        extractor = rexpy.Extractor(dirs)
        extractor.extract()
        regex = extractor.results.rex[0]
        return regex

    def set_token_ordering(self):
        for token_idx, lineedit in enumerate(self.levels.values()):
            self.tokenordering[token_idx] = lineedit.text()

    # Convenience function for updating
    @Slot()
    def update_sibling_awareness(self):
        current_texts = [le.text() for le in self.levels.values()]
        for le in self.levels.values():
            le.sibling_awareness = current_texts

    def run_importer(self):
        print(f"Regex for Subjects: {self.subject_regex}")
        print(f"Regex for Sessions: {self.session_regex}")
        print(f"Regex for Scans: {self.scan_regex}")
        print(f"Session Dict: {self.session_aliases}")
        print(f"Scan Dict: {self.scan_aliases}")
        self.set_token_ordering()
        print(f"Token Ordering: {self.tokenordering}")


class DraggableLabel(QLabel):
    """
    Modified QLabel to support dragging out the text content
    """

    def __init__(self, text='', parent=None):
        super(DraggableLabel, self).__init__(parent)
        self.setText(text)
        self.setStyleSheet("border-style: solid;"
                           "border-width: 2px;"
                           "border-color: black;"
                           "border-radius: 10px;"
                           "background-color: white;")
        font = QFont()
        font.setPointSize(16)
        self.setFont(font)
        self.setMinimumHeight(75)
        self.setMaximumHeight(100)
        self.setAlignment(Qt.AlignCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_position = event.pos()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        if (event.pos() - self.drag_start_position).manhattanLength() < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mimedata = QMimeData()
        mimedata.setText(self.text())
        drag.setMimeData(mimedata)
        drag.setHotSpot(event.pos())
        drag.exec_(Qt.CopyAction | Qt.MoveAction)


class DandD_Label2LineEdit(QLineEdit):
    """
    Modified QLineEdit to support accepting text drops from a QLabel with Drag enabled
    """

    def __init__(self, superparent, parent=None):
        super().__init__(parent)

        self.setAcceptDrops(True)
        self.setReadOnly(True)
        self.superparent = superparent  # This is the Importer Widget itself
        self.sibling_awareness = ['', '', '']

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasText():
            if event.mimeData().text() not in self.sibling_awareness and self.superparent.le_rootdir.text() != '':
                event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasText():
            if event.mimeData().text() not in self.sibling_awareness and self.superparent.le_rootdir.text() != '':
                event.accept()
                event.setDropAction(Qt.CopyAction)
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasText():
            if event.mimeData().text() not in self.sibling_awareness and self.superparent.le_rootdir.text() != '':
                event.accept()
                self.setText(event.mimeData().text())
        else:
            event.ignore()