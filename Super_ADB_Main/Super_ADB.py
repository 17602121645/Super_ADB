# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'Super_ADB.ui'
##
## Created by: Qt User Interface Compiler version 6.11.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QSpacerItem, QSplitter,
    QStatusBar, QTextEdit, QTreeView, QVBoxLayout,
    QWidget)

from fav_combo import FavComboBox
import png_rc

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(1659, 755)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(1)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(MainWindow.sizePolicy().hasHeightForWidth())
        MainWindow.setSizePolicy(sizePolicy)
        MainWindow.setMinimumSize(QSize(100, 100))
        self.verticalLayout_2 = QVBoxLayout(MainWindow)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.horizontalLayout_4 = QHBoxLayout()
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.horizontalSpacer_7 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_4.addItem(self.horizontalSpacer_7)

        self.winBtnClose = QPushButton(MainWindow)
        self.winBtnClose.setObjectName(u"winBtnClose")
        self.winBtnClose.setMinimumSize(QSize(34, 26))
        self.winBtnClose.setMaximumSize(QSize(34, 26))
        self.winBtnClose.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        icon = QIcon(QIcon.fromTheme(QIcon.ThemeIcon.ObjectRotateRight))
        self.winBtnClose.setIcon(icon)

        self.horizontalLayout_4.addWidget(self.winBtnClose)

        self.horizontalLayout_3 = QHBoxLayout()
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")

        self.horizontalLayout_4.addLayout(self.horizontalLayout_3)


        self.verticalLayout_2.addLayout(self.horizontalLayout_4)

        self.splitter_2 = QSplitter(MainWindow)
        self.splitter_2.setObjectName(u"splitter_2")
        self.splitter_2.setOrientation(Qt.Orientation.Horizontal)
        self.leftPanel = QWidget(self.splitter_2)
        self.leftPanel.setObjectName(u"leftPanel")
        self.verticalLayout_3 = QVBoxLayout(self.leftPanel)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.verticalLayout_3.setContentsMargins(0, 0, 0, 0)
        self.groupBox = QGroupBox(self.leftPanel)
        self.groupBox.setObjectName(u"groupBox")
        self.horizontalLayout_2 = QHBoxLayout(self.groupBox)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.lblDevice = QLabel(self.groupBox)
        self.lblDevice.setObjectName(u"lblDevice")

        self.horizontalLayout_2.addWidget(self.lblDevice)

        self.deviceCombo = QComboBox(self.groupBox)
        self.deviceCombo.setObjectName(u"deviceCombo")
        self.deviceCombo.setMinimumSize(QSize(240, 0))

        self.horizontalLayout_2.addWidget(self.deviceCombo)

        self.btnRefresh = QPushButton(self.groupBox)
        self.btnRefresh.setObjectName(u"btnRefresh")

        self.horizontalLayout_2.addWidget(self.btnRefresh)

        self.btnDisconnect = QPushButton(self.groupBox)
        self.btnDisconnect.setObjectName(u"btnDisconnect")

        self.horizontalLayout_2.addWidget(self.btnDisconnect)


        self.verticalLayout_3.addWidget(self.groupBox)

        self.connGroup = QGroupBox(self.leftPanel)
        self.connGroup.setObjectName(u"connGroup")
        self.connLayout = QHBoxLayout(self.connGroup)
        self.connLayout.setObjectName(u"connLayout")
        self.lblConn = QLabel(self.connGroup)
        self.lblConn.setObjectName(u"lblConn")

        self.connLayout.addWidget(self.lblConn)

        self.ipInput = QLineEdit(self.connGroup)
        self.ipInput.setObjectName(u"ipInput")

        self.connLayout.addWidget(self.ipInput)

        self.btnConnect = QPushButton(self.connGroup)
        self.btnConnect.setObjectName(u"btnConnect")

        self.connLayout.addWidget(self.btnConnect)

        self.lanScanBtn = QPushButton(self.connGroup)
        self.lanScanBtn.setObjectName(u"lanScanBtn")

        self.connLayout.addWidget(self.lanScanBtn)

        self.btnWifiPair = QPushButton(self.connGroup)
        self.btnWifiPair.setObjectName(u"btnWifiPair")

        self.connLayout.addWidget(self.btnWifiPair)


        self.verticalLayout_3.addWidget(self.connGroup)

        self.sysGroup = QGroupBox(self.leftPanel)
        self.sysGroup.setObjectName(u"sysGroup")
        self.gridLayout = QGridLayout(self.sysGroup)
        self.gridLayout.setObjectName(u"gridLayout")
        self.pcIpCell = QWidget(self.sysGroup)
        self.pcIpCell.setObjectName(u"pcIpCell")
        self.pcIpLayout = QHBoxLayout(self.pcIpCell)
        self.pcIpLayout.setObjectName(u"pcIpLayout")
        self.pcIpLayout.setContentsMargins(0, 0, 0, 0)
        self.pcIpLabel = QLabel(self.pcIpCell)
        self.pcIpLabel.setObjectName(u"pcIpLabel")

        self.pcIpLayout.addWidget(self.pcIpLabel)

        self.pcIpInput = QLineEdit(self.pcIpCell)
        self.pcIpInput.setObjectName(u"pcIpInput")

        self.pcIpLayout.addWidget(self.pcIpInput)

        self.btnTcpdump = QPushButton(self.pcIpCell)
        self.btnTcpdump.setObjectName(u"btnTcpdump")

        self.pcIpLayout.addWidget(self.btnTcpdump)


        self.gridLayout.addWidget(self.pcIpCell, 0, 0, 1, 4)

        self.btnReboot = QPushButton(self.sysGroup)
        self.btnReboot.setObjectName(u"btnReboot")

        self.gridLayout.addWidget(self.btnReboot, 1, 2, 1, 1)

        self.btnSetProxy = QPushButton(self.sysGroup)
        self.btnSetProxy.setObjectName(u"btnSetProxy")

        self.gridLayout.addWidget(self.btnSetProxy, 1, 0, 1, 1)

        self.btnSystemRoot = QPushButton(self.sysGroup)
        self.btnSystemRoot.setObjectName(u"btnSystemRoot")

        self.gridLayout.addWidget(self.btnSystemRoot, 1, 3, 1, 1)

        self.btnClearProxy = QPushButton(self.sysGroup)
        self.btnClearProxy.setObjectName(u"btnClearProxy")

        self.gridLayout.addWidget(self.btnClearProxy, 1, 1, 1, 1)

        self.btnDeviceInfo = QPushButton(self.sysGroup)
        self.btnDeviceInfo.setObjectName(u"btnDeviceInfo")

        self.gridLayout.addWidget(self.btnDeviceInfo, 2, 0, 1, 1)

        self.btnDpm = QPushButton(self.sysGroup)
        self.btnDpm.setObjectName(u"btnDpm")

        self.gridLayout.addWidget(self.btnDpm, 3, 0, 1, 1)

        self.btnRunningApps = QPushButton(self.sysGroup)
        self.btnRunningApps.setObjectName(u"btnRunningApps")

        self.gridLayout.addWidget(self.btnRunningApps, 2, 1, 1, 1)

        self.btnApps3 = QPushButton(self.sysGroup)
        self.btnApps3.setObjectName(u"btnApps3")

        self.gridLayout.addWidget(self.btnApps3, 2, 2, 1, 1)

        self.btnAppsS = QPushButton(self.sysGroup)
        self.btnAppsS.setObjectName(u"btnAppsS")

        self.gridLayout.addWidget(self.btnAppsS, 2, 3, 1, 1)

        self.btnInputText = QPushButton(self.sysGroup)
        self.btnInputText.setObjectName(u"btnInputText")

        self.gridLayout.addWidget(self.btnInputText, 3, 1, 1, 1)

        self.btnWindowApp = QPushButton(self.sysGroup)
        self.btnWindowApp.setObjectName(u"btnWindowApp")

        self.gridLayout.addWidget(self.btnWindowApp, 3, 2, 1, 1)

        self.btnAppsAll = QPushButton(self.sysGroup)
        self.btnAppsAll.setObjectName(u"btnAppsAll")

        self.gridLayout.addWidget(self.btnAppsAll, 3, 3, 1, 1)


        self.verticalLayout_3.addWidget(self.sysGroup)

        self.appGroup = QGroupBox(self.leftPanel)
        self.appGroup.setObjectName(u"appGroup")
        self.gridLayout_2 = QGridLayout(self.appGroup)
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        self.btnStopApp = QPushButton(self.appGroup)
        self.btnStopApp.setObjectName(u"btnStopApp")

        self.gridLayout_2.addWidget(self.btnStopApp, 0, 4, 1, 1)

        self.btnRunningApps_2 = QPushButton(self.appGroup)
        self.btnRunningApps_2.setObjectName(u"btnRunningApps_2")

        self.gridLayout_2.addWidget(self.btnRunningApps_2, 1, 6, 1, 1)

        self.btnUninstall = QPushButton(self.appGroup)
        self.btnUninstall.setObjectName(u"btnUninstall")

        self.gridLayout_2.addWidget(self.btnUninstall, 1, 1, 1, 1)

        self.btnClearApp = QPushButton(self.appGroup)
        self.btnClearApp.setObjectName(u"btnClearApp")

        self.gridLayout_2.addWidget(self.btnClearApp, 0, 6, 1, 1)

        self.lblPkg = QLabel(self.appGroup)
        self.lblPkg.setObjectName(u"lblPkg")
        self.lblPkg.setMaximumSize(QSize(30, 50))

        self.gridLayout_2.addWidget(self.lblPkg, 0, 0, 1, 1)

        self.btnStartApp = QPushButton(self.appGroup)
        self.btnStartApp.setObjectName(u"btnStartApp")

        self.gridLayout_2.addWidget(self.btnStartApp, 0, 5, 1, 1)

        self.btnAppInfo = QPushButton(self.appGroup)
        self.btnAppInfo.setObjectName(u"btnAppInfo")

        self.gridLayout_2.addWidget(self.btnAppInfo, 1, 2, 1, 1)

        self.btnpm = QPushButton(self.appGroup)
        self.btnpm.setObjectName(u"btnpm")

        self.gridLayout_2.addWidget(self.btnpm, 1, 4, 1, 1)

        self.btnMeminfo = QPushButton(self.appGroup)
        self.btnMeminfo.setObjectName(u"btnMeminfo")

        self.gridLayout_2.addWidget(self.btnMeminfo, 1, 5, 1, 1)

        self.btninstallzip = QPushButton(self.appGroup)
        self.btninstallzip.setObjectName(u"btninstallzip")

        self.gridLayout_2.addWidget(self.btninstallzip, 1, 0, 1, 1)

        self.pkgInput = QLineEdit(self.appGroup)
        self.pkgInput.setObjectName(u"pkgInput")

        self.gridLayout_2.addWidget(self.pkgInput, 0, 1, 1, 2)


        self.verticalLayout_3.addWidget(self.appGroup)

        self.toolsGroup = QGroupBox(self.leftPanel)
        self.toolsGroup.setObjectName(u"toolsGroup")
        self.gridLayout_tools = QGridLayout(self.toolsGroup)
        self.gridLayout_tools.setObjectName(u"gridLayout_tools")
        self.cmdBtn = QPushButton(self.toolsGroup)
        self.cmdBtn.setObjectName(u"cmdBtn")

        self.gridLayout_tools.addWidget(self.cmdBtn, 0, 0, 1, 1)

        self.jsonToolBtn = QPushButton(self.toolsGroup)
        self.jsonToolBtn.setObjectName(u"jsonToolBtn")

        self.gridLayout_tools.addWidget(self.jsonToolBtn, 0, 1, 1, 1)

        self.md5Btn = QPushButton(self.toolsGroup)
        self.md5Btn.setObjectName(u"md5Btn")

        self.gridLayout_tools.addWidget(self.md5Btn, 0, 2, 1, 1)

        self.timestampBtn = QPushButton(self.toolsGroup)
        self.timestampBtn.setObjectName(u"timestampBtn")

        self.gridLayout_tools.addWidget(self.timestampBtn, 0, 3, 1, 1)

        self.wifiBtn = QPushButton(self.toolsGroup)
        self.wifiBtn.setObjectName(u"wifiBtn")

        self.gridLayout_tools.addWidget(self.wifiBtn, 0, 4, 1, 1)


        self.verticalLayout_3.addWidget(self.toolsGroup)

        self.outGroup = QGroupBox(self.leftPanel)
        self.outGroup.setObjectName(u"outGroup")
        self.outLayout = QVBoxLayout(self.outGroup)
        self.outLayout.setObjectName(u"outLayout")
        self.output = QTextEdit(self.outGroup)
        self.output.setObjectName(u"output")
        self.output.setReadOnly(True)

        self.outLayout.addWidget(self.output)

        self.outBtnRow = QHBoxLayout()
        self.outBtnRow.setObjectName(u"outBtnRow")
        self.btnClear = QPushButton(self.outGroup)
        self.btnClear.setObjectName(u"btnClear")

        self.outBtnRow.addWidget(self.btnClear)

        self.btnCopy = QPushButton(self.outGroup)
        self.btnCopy.setObjectName(u"btnCopy")

        self.outBtnRow.addWidget(self.btnCopy)

        self.outBtnSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.outBtnRow.addItem(self.outBtnSpacer)


        self.outLayout.addLayout(self.outBtnRow)


        self.verticalLayout_3.addWidget(self.outGroup)

        self.splitter_2.addWidget(self.leftPanel)
        self.splitter = QSplitter(self.splitter_2)
        self.splitter.setObjectName(u"splitter")
        self.splitter.setOrientation(Qt.Orientation.Vertical)
        self.layoutWidget = QWidget(self.splitter)
        self.layoutWidget.setObjectName(u"layoutWidget")
        self.verticalLayout = QVBoxLayout(self.layoutWidget)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.fileMgrLblDevice = QLabel(self.layoutWidget)
        self.fileMgrLblDevice.setObjectName(u"fileMgrLblDevice")

        self.horizontalLayout.addWidget(self.fileMgrLblDevice)

        self.fileMgr_deviceCombo = QComboBox(self.layoutWidget)
        self.fileMgr_deviceCombo.setObjectName(u"fileMgr_deviceCombo")
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.fileMgr_deviceCombo.sizePolicy().hasHeightForWidth())
        self.fileMgr_deviceCombo.setSizePolicy(sizePolicy1)
        self.fileMgr_deviceCombo.setMinimumSize(QSize(250, 0))

        self.horizontalLayout.addWidget(self.fileMgr_deviceCombo)

        self.fileMgr_btnRefresh = QPushButton(self.layoutWidget)
        self.fileMgr_btnRefresh.setObjectName(u"fileMgr_btnRefresh")

        self.horizontalLayout.addWidget(self.fileMgr_btnRefresh)

        self.horizontalSpacer_5 = QSpacerItem(40, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)

        self.horizontalLayout.addItem(self.horizontalSpacer_5)

        self.fileMgr_btnRoot = QPushButton(self.layoutWidget)
        self.fileMgr_btnRoot.setObjectName(u"fileMgr_btnRoot")

        self.horizontalLayout.addWidget(self.fileMgr_btnRoot)

        self.fileMgrBarSpacer = QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        self.horizontalLayout.addItem(self.fileMgrBarSpacer)

        self.fileMgr_pathLabel = QLabel(self.layoutWidget)
        self.fileMgr_pathLabel.setObjectName(u"fileMgr_pathLabel")

        self.horizontalLayout.addWidget(self.fileMgr_pathLabel)

        self.fileMgr_statusLabel = QLabel(self.layoutWidget)
        self.fileMgr_statusLabel.setObjectName(u"fileMgr_statusLabel")

        self.horizontalLayout.addWidget(self.fileMgr_statusLabel)


        self.verticalLayout.addLayout(self.horizontalLayout)

        self.fileMgr_tree = QTreeView(self.layoutWidget)
        self.fileMgr_tree.setObjectName(u"fileMgr_tree")
        self.fileMgr_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fileMgr_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.fileMgr_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.verticalLayout.addWidget(self.fileMgr_tree)

        self.splitter.addWidget(self.layoutWidget)
        self.layoutWidget1 = QWidget(self.splitter)
        self.layoutWidget1.setObjectName(u"layoutWidget1")
        self.logViewerLayout = QVBoxLayout(self.layoutWidget1)
        self.logViewerLayout.setSpacing(6)
        self.logViewerLayout.setObjectName(u"logViewerLayout")
        self.logViewerLayout.setContentsMargins(8, 8, 8, 8)
        self.logViewerBar = QHBoxLayout()
        self.logViewerBar.setObjectName(u"logViewerBar")
        self.logViewerLblDevice = QLabel(self.layoutWidget1)
        self.logViewerLblDevice.setObjectName(u"logViewerLblDevice")

        self.logViewerBar.addWidget(self.logViewerLblDevice)

        self.logViewer_deviceCombo = QComboBox(self.layoutWidget1)
        self.logViewer_deviceCombo.setObjectName(u"logViewer_deviceCombo")
        self.logViewer_deviceCombo.setMinimumSize(QSize(150, 0))

        self.logViewerBar.addWidget(self.logViewer_deviceCombo)

        self.logViewer_btnRefresh = QPushButton(self.layoutWidget1)
        self.logViewer_btnRefresh.setObjectName(u"logViewer_btnRefresh")

        self.logViewerBar.addWidget(self.logViewer_btnRefresh)

        self.horizontalSpacer_6 = QSpacerItem(40, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)

        self.logViewerBar.addItem(self.horizontalSpacer_6)

        self.logViewer_btnStart = QPushButton(self.layoutWidget1)
        self.logViewer_btnStart.setObjectName(u"logViewer_btnStart")

        self.logViewerBar.addWidget(self.logViewer_btnStart)

        self.logViewer_btnPause = QPushButton(self.layoutWidget1)
        self.logViewer_btnPause.setObjectName(u"logViewer_btnPause")
        self.logViewer_btnPause.setEnabled(False)

        self.logViewerBar.addWidget(self.logViewer_btnPause)

        self.logViewer_btnClear = QPushButton(self.layoutWidget1)
        self.logViewer_btnClear.setObjectName(u"logViewer_btnClear")

        self.logViewerBar.addWidget(self.logViewer_btnClear)

        self.btnLf = QPushButton(self.layoutWidget1)
        self.btnLf.setObjectName(u"btnLf")

        self.logViewerBar.addWidget(self.btnLf)

        self.horizontalSpacer = QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        self.logViewerBar.addItem(self.horizontalSpacer)

        self.logViewer_statusLabel = QLabel(self.layoutWidget1)
        self.logViewer_statusLabel.setObjectName(u"logViewer_statusLabel")

        self.logViewerBar.addWidget(self.logViewer_statusLabel)


        self.logViewerLayout.addLayout(self.logViewerBar)

        self.logFilterBar = QHBoxLayout()
        self.logFilterBar.setObjectName(u"logFilterBar")
        self.logFilterLblTag = QLabel(self.layoutWidget1)
        self.logFilterLblTag.setObjectName(u"logFilterLblTag")

        self.logFilterBar.addWidget(self.logFilterLblTag)

        self.logViewer_tagCombo = FavComboBox(self.layoutWidget1)
        self.logViewer_tagCombo.setObjectName(u"logViewer_tagCombo")
        sizePolicy2 = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sizePolicy2.setHorizontalStretch(1)
        sizePolicy2.setVerticalStretch(0)
        sizePolicy2.setHeightForWidth(self.logViewer_tagCombo.sizePolicy().hasHeightForWidth())
        self.logViewer_tagCombo.setSizePolicy(sizePolicy2)
        self.logViewer_tagCombo.setMinimumSize(QSize(250, 0))

        self.logFilterBar.addWidget(self.logViewer_tagCombo)

        self.logViewer_tagStar = QPushButton(self.layoutWidget1)
        self.logViewer_tagStar.setObjectName(u"logViewer_tagStar")
        self.logViewer_tagStar.setMaximumSize(QSize(30, 30))

        self.logFilterBar.addWidget(self.logViewer_tagStar)

        self.logFilterLblPid = QLabel(self.layoutWidget1)
        self.logFilterLblPid.setObjectName(u"logFilterLblPid")

        self.logFilterBar.addWidget(self.logFilterLblPid)

        self.logViewer_procCombo = FavComboBox(self.layoutWidget1)
        self.logViewer_procCombo.setObjectName(u"logViewer_procCombo")
        sizePolicy2.setHeightForWidth(self.logViewer_procCombo.sizePolicy().hasHeightForWidth())
        self.logViewer_procCombo.setSizePolicy(sizePolicy2)
        self.logViewer_procCombo.setMinimumSize(QSize(250, 0))

        self.logFilterBar.addWidget(self.logViewer_procCombo)

        self.logViewer_procStar = QPushButton(self.layoutWidget1)
        self.logViewer_procStar.setObjectName(u"logViewer_procStar")
        self.logViewer_procStar.setMaximumSize(QSize(30, 30))

        self.logFilterBar.addWidget(self.logViewer_procStar)

        self.logFilterLblMsg = QLabel(self.layoutWidget1)
        self.logFilterLblMsg.setObjectName(u"logFilterLblMsg")

        self.logFilterBar.addWidget(self.logFilterLblMsg)

        self.logViewer_msgCombo = FavComboBox(self.layoutWidget1)
        self.logViewer_msgCombo.setObjectName(u"logViewer_msgCombo")
        sizePolicy2.setHeightForWidth(self.logViewer_msgCombo.sizePolicy().hasHeightForWidth())
        self.logViewer_msgCombo.setSizePolicy(sizePolicy2)
        self.logViewer_msgCombo.setMinimumSize(QSize(250, 0))

        self.logFilterBar.addWidget(self.logViewer_msgCombo)

        self.logViewer_msgStar = QPushButton(self.layoutWidget1)
        self.logViewer_msgStar.setObjectName(u"logViewer_msgStar")
        self.logViewer_msgStar.setMaximumSize(QSize(30, 30))

        self.logFilterBar.addWidget(self.logViewer_msgStar)

        self.logViewer_regexChk = QCheckBox(self.layoutWidget1)
        self.logViewer_regexChk.setObjectName(u"logViewer_regexChk")

        self.logFilterBar.addWidget(self.logViewer_regexChk)

        self.horizontalSpacer_4 = QSpacerItem(40, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)

        self.logFilterBar.addItem(self.horizontalSpacer_4)

        self.logViewer_btnReset = QPushButton(self.layoutWidget1)
        self.logViewer_btnReset.setObjectName(u"logViewer_btnReset")

        self.logFilterBar.addWidget(self.logViewer_btnReset)


        self.logViewerLayout.addLayout(self.logFilterBar)

        self.logViewer_textEdit = QListWidget(self.layoutWidget1)
        self.logViewer_textEdit.setObjectName(u"logViewer_textEdit")

        self.logViewerLayout.addWidget(self.logViewer_textEdit)

        self.logViewerBot = QHBoxLayout()
        self.logViewerBot.setObjectName(u"logViewerBot")
        self.logViewer_followChk = QCheckBox(self.layoutWidget1)
        self.logViewer_followChk.setObjectName(u"logViewer_followChk")
        self.logViewer_followChk.setChecked(True)

        self.logViewerBot.addWidget(self.logViewer_followChk)

        self.logViewer_modeLabel = QLabel(self.layoutWidget1)
        self.logViewer_modeLabel.setObjectName(u"logViewer_modeLabel")

        self.logViewerBot.addWidget(self.logViewer_modeLabel)

        self.logViewerBotSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)

        self.logViewerBot.addItem(self.logViewerBotSpacer)

        self.logViewer_countLabel = QLabel(self.layoutWidget1)
        self.logViewer_countLabel.setObjectName(u"logViewer_countLabel")

        self.logViewerBot.addWidget(self.logViewer_countLabel)


        self.logViewerLayout.addLayout(self.logViewerBot)

        self.splitter.addWidget(self.layoutWidget1)
        self.splitter_2.addWidget(self.splitter)

        self.verticalLayout_2.addWidget(self.splitter_2)

        self.statusBar = QStatusBar(MainWindow)
        self.statusBar.setObjectName(u"statusBar")

        self.verticalLayout_2.addWidget(self.statusBar)


        self.retranslateUi(MainWindow)
        self.winBtnClose.clicked.connect(MainWindow.hide)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"Super_ADB", None))
#if QT_CONFIG(tooltip)
        MainWindow.setToolTip("")
#endif // QT_CONFIG(tooltip)
#if QT_CONFIG(tooltip)
        self.winBtnClose.setToolTip(QCoreApplication.translate("MainWindow", u"\u9690\u85cf\u5230\u7cfb\u7edf\u6258\u76d8", None))
#endif // QT_CONFIG(tooltip)
        self.winBtnClose.setText("")
        self.groupBox.setTitle(QCoreApplication.translate("MainWindow", u"\u8fde\u63a5\u7684\u8bbe\u5907", None))
        self.lblDevice.setText(QCoreApplication.translate("MainWindow", u"\u8fde\u63a5\u7684\u8bbe\u5907:", None))
        self.btnRefresh.setText(QCoreApplication.translate("MainWindow", u"\u5237\u65b0\u8fde\u63a5\u5217\u8868", None))
        self.btnDisconnect.setText(QCoreApplication.translate("MainWindow", u"\u65ad\u5f00\u8fde\u63a5", None))
        self.connGroup.setTitle(QCoreApplication.translate("MainWindow", u"\u8bbe\u5907\u8fde\u63a5", None))
        self.lblConn.setText(QCoreApplication.translate("MainWindow", u"\u5efa\u7acb\u8fde\u63a5:", None))
        self.ipInput.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u8f93\u5165IP\uff08\u65e0\u6cd5\u8fde\u63a5\u5c1d\u8bd5\u52a0 :5555\uff09", None))
        self.btnConnect.setText(QCoreApplication.translate("MainWindow", u"\u8fde\u63a5", None))
        self.lanScanBtn.setText(QCoreApplication.translate("MainWindow", u"\u5c40\u57df\u7f51\u626b\u63cf", None))
#if QT_CONFIG(tooltip)
        self.lanScanBtn.setToolTip(QCoreApplication.translate("MainWindow", u"\u626b\u63cf\u5c40\u57df\u7f51\u5185\u53ef\u8fde\u63a5\u7684 ADB \u8bbe\u5907\uff08\u7aef\u53e3 5555\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnWifiPair.setText(QCoreApplication.translate("MainWindow", u"WiFi \u914d\u5bf9", None))
#if QT_CONFIG(tooltip)
        self.btnWifiPair.setToolTip(QCoreApplication.translate("MainWindow", u"\u901a\u8fc7 Android 11+ \u65e0\u7ebf\u8c03\u8bd5\u914d\u5bf9\u7801\u5b8c\u6210\u7ed1\u5b9a", None))
#endif // QT_CONFIG(tooltip)
        self.sysGroup.setTitle(QCoreApplication.translate("MainWindow", u"\u7cfb\u7edf\u64cd\u4f5c", None))
        self.pcIpLabel.setText(QCoreApplication.translate("MainWindow", u"PC\u672c\u673aIP", None))
        self.pcIpInput.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u672c\u673aIP:\u7aef\u53e3", None))
        self.btnTcpdump.setText(QCoreApplication.translate("MainWindow", u"tcpdump \u6293\u5305", None))
        self.btnReboot.setText(QCoreApplication.translate("MainWindow", u"\u8bbe\u5907\u91cd\u542f", None))
        self.btnSetProxy.setText(QCoreApplication.translate("MainWindow", u"\u8bbe\u7f6e\u4ee3\u7406", None))
        self.btnSystemRoot.setText(QCoreApplication.translate("MainWindow", u"system\u8bfb\u5199", None))
        self.btnClearProxy.setText(QCoreApplication.translate("MainWindow", u"\u53d6\u6d88\u4ee3\u7406", None))
        self.btnDeviceInfo.setText(QCoreApplication.translate("MainWindow", u"\u83b7\u53d6\u8bbe\u5907\u4fe1\u606f", None))
        self.btnDpm.setText(QCoreApplication.translate("MainWindow", u"\u8bbe\u5907\u6027\u80fd\u76d1\u63a7", None))
        self.btnRunningApps.setText(QCoreApplication.translate("MainWindow", u"\u8fd0\u884c\u4e2d\u5217\u8868", None))
        self.btnApps3.setText(QCoreApplication.translate("MainWindow", u"\u7b2c\u4e09\u65b9\u5305", None))
        self.btnAppsS.setText(QCoreApplication.translate("MainWindow", u"\u7cfb\u7edf\u5305", None))
        self.btnInputText.setText(QCoreApplication.translate("MainWindow", u"\u8f93\u5165\u6587\u672c", None))
        self.btnWindowApp.setText(QCoreApplication.translate("MainWindow", u"\u754c\u9762\u5305\u83b7\u53d6", None))
        self.btnAppsAll.setText(QCoreApplication.translate("MainWindow", u"\u6240\u6709\u5305", None))
        self.appGroup.setTitle(QCoreApplication.translate("MainWindow", u"\u5e94\u7528\u64cd\u4f5c", None))
        self.btnStopApp.setText(QCoreApplication.translate("MainWindow", u"\u5173\u95ed", None))
        self.btnRunningApps_2.setText(QCoreApplication.translate("MainWindow", u"Monkey", None))
        self.btnUninstall.setText(QCoreApplication.translate("MainWindow", u"\u5378\u8f7d", None))
        self.btnClearApp.setText(QCoreApplication.translate("MainWindow", u"\u6e05\u7406\u6570\u636e", None))
        self.lblPkg.setText(QCoreApplication.translate("MainWindow", u"\u5305\u540d:", None))
        self.btnStartApp.setText(QCoreApplication.translate("MainWindow", u"\u542f\u52a8", None))
        self.btnAppInfo.setText(QCoreApplication.translate("MainWindow", u"path/pid", None))
        self.btnpm.setText(QCoreApplication.translate("MainWindow", u"\u5e94\u7528\u76d1\u63a7", None))
        self.btnMeminfo.setText(QCoreApplication.translate("MainWindow", u"\u8fd0\u884c\u5185\u5b58", None))
        self.btninstallzip.setText(QCoreApplication.translate("MainWindow", u"\u5b89\u88c5/\u89e3\u5305", None))
        self.pkgInput.setPlaceholderText(QCoreApplication.translate("MainWindow", u"com.example.app", None))
        self.toolsGroup.setTitle(QCoreApplication.translate("MainWindow", u"\u4fbf\u6377\u5de5\u5177", None))
        self.cmdBtn.setText(QCoreApplication.translate("MainWindow", u"\u547d\u4ee4\u884c", None))
#if QT_CONFIG(tooltip)
        self.cmdBtn.setToolTip(QCoreApplication.translate("MainWindow", u"\u6253\u5f00\u7cfb\u7edf PowerShell\uff08Windows\uff09/ \u7ec8\u7aef\uff08macOS, Linux\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.jsonToolBtn.setText(QCoreApplication.translate("MainWindow", u"JSON\u5de5\u5177", None))
#if QT_CONFIG(tooltip)
        self.jsonToolBtn.setToolTip(QCoreApplication.translate("MainWindow", u"JSON \u683c\u5f0f\u5316/\u538b\u7f29 + \u5dee\u5f02\u5bf9\u6bd4", None))
#endif // QT_CONFIG(tooltip)
        self.md5Btn.setText(QCoreApplication.translate("MainWindow", u"MD5", None))
#if QT_CONFIG(tooltip)
        self.md5Btn.setToolTip(QCoreApplication.translate("MainWindow", u"\u6587\u4ef6 MD5 / SHA1 / SHA256 \u6821\u9a8c\uff08\u62d6\u5165\u6587\u4ef6\u5373\u53ef\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.timestampBtn.setText(QCoreApplication.translate("MainWindow", u"\u65f6\u95f4\u6233\u8f6c\u6362", None))
#if QT_CONFIG(tooltip)
        self.timestampBtn.setToolTip(QCoreApplication.translate("MainWindow", u"Unix \u65f6\u95f4\u6233 \u2194 \u5317\u4eac\u65f6\u95f4 \u4e92\u8f6c", None))
#endif // QT_CONFIG(tooltip)
        self.wifiBtn.setText(QCoreApplication.translate("MainWindow", u"WiFi\u5bc6\u7801", None))
#if QT_CONFIG(tooltip)
        self.wifiBtn.setToolTip(QCoreApplication.translate("MainWindow", u"\u67e5\u770b\u672c\u673a\u5df2\u4fdd\u5b58\u7684 WiFi \u53ca\u5bc6\u7801", None))
#endif // QT_CONFIG(tooltip)
        self.outGroup.setTitle(QCoreApplication.translate("MainWindow", u"\u8f93\u51fa", None))
        self.output.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u547d\u4ee4\u8f93\u51fa\u5c06\u663e\u793a\u5728\u8fd9\u91cc...", None))
        self.btnClear.setText(QCoreApplication.translate("MainWindow", u"\u6e05\u9664", None))
        self.btnCopy.setText(QCoreApplication.translate("MainWindow", u"\u590d\u5236", None))
        self.fileMgrLblDevice.setText(QCoreApplication.translate("MainWindow", u"\u8bbe\u5907:", None))
        self.fileMgr_btnRefresh.setText(QCoreApplication.translate("MainWindow", u"\u5237\u65b0\u8bbe\u5907", None))
        self.fileMgr_btnRoot.setText(QCoreApplication.translate("MainWindow", u"\u6839\u76ee\u5f55: /sdcard", None))
        self.fileMgr_pathLabel.setText(QCoreApplication.translate("MainWindow", u"\u2014", None))
        self.fileMgr_statusLabel.setText(QCoreApplication.translate("MainWindow", u"\u5c31\u7eea", None))
        self.logViewerLblDevice.setText(QCoreApplication.translate("MainWindow", u"\u8bbe\u5907:", None))
        self.logViewer_btnRefresh.setText(QCoreApplication.translate("MainWindow", u"\u5237\u65b0", None))
        self.logViewer_btnStart.setText(QCoreApplication.translate("MainWindow", u"\u5f00\u59cb\u6293\u53d6", None))
        self.logViewer_btnPause.setText(QCoreApplication.translate("MainWindow", u"\u6682\u505c", None))
        self.logViewer_btnClear.setText(QCoreApplication.translate("MainWindow", u"\u6e05\u9664", None))
        self.btnLf.setText(QCoreApplication.translate("MainWindow", u"\u6253\u5f00\u672c\u5730\u6587\u4ef6", None))
        self.logViewer_statusLabel.setText(QCoreApplication.translate("MainWindow", u"\u5c31\u7eea", None))
        self.logFilterLblTag.setText(QCoreApplication.translate("MainWindow", u"\u6807\u7b7e:", None))
        self.logViewer_tagCombo.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u65e5\u5fd7 TAG", None))
#if QT_CONFIG(tooltip)
        self.logViewer_tagStar.setToolTip(QCoreApplication.translate("MainWindow", u"\u628a\u5f53\u524d\u8f93\u5165\u52a0\u5165\u6536\u85cf", None))
#endif // QT_CONFIG(tooltip)
        self.logViewer_tagStar.setText(QCoreApplication.translate("MainWindow", u"\u2605", None))
        self.logFilterLblPid.setText(QCoreApplication.translate("MainWindow", u"\u5305\u540d:", None))
        self.logViewer_procCombo.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u5305\u540d\uff0c\u5982 com.xxx.app\uff0c\u7a7a\u683c\u5206\u9694\u591a\u4e2a", None))
#if QT_CONFIG(tooltip)
        self.logViewer_procStar.setToolTip(QCoreApplication.translate("MainWindow", u"\u628a\u5f53\u524d\u8f93\u5165\u52a0\u5165\u6536\u85cf", None))
#endif // QT_CONFIG(tooltip)
        self.logViewer_procStar.setText(QCoreApplication.translate("MainWindow", u"\u2605", None))
        self.logFilterLblMsg.setText(QCoreApplication.translate("MainWindow", u"\u6d88\u606f:", None))
        self.logViewer_msgCombo.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u641c\u7d22\u5173\u952e\u5b57", None))
#if QT_CONFIG(tooltip)
        self.logViewer_msgStar.setToolTip(QCoreApplication.translate("MainWindow", u"\u628a\u5f53\u524d\u8f93\u5165\u52a0\u5165\u6536\u85cf", None))
#endif // QT_CONFIG(tooltip)
        self.logViewer_msgStar.setText(QCoreApplication.translate("MainWindow", u"\u2605", None))
        self.logViewer_regexChk.setText(QCoreApplication.translate("MainWindow", u"\u6b63\u5219", None))
#if QT_CONFIG(tooltip)
        self.logViewer_regexChk.setToolTip(QCoreApplication.translate("MainWindow", u"\u52fe\u9009\u540e\"\u6d88\u606f\"\u8fc7\u6ee4\u6846\u6309\u6b63\u5219\u8868\u8fbe\u5f0f\u5339\u914d\uff08re.search\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.logViewer_btnReset.setText(QCoreApplication.translate("MainWindow", u"\u91cd\u7f6e", None))
        self.logViewer_followChk.setText(QCoreApplication.translate("MainWindow", u"\u8ddf\u968f\u6eda\u52a8", None))
        self.logViewer_modeLabel.setText(QCoreApplication.translate("MainWindow", u"\u672a\u52a0\u8f7d\u65e5\u5fd7", None))
        self.logViewer_countLabel.setText(QCoreApplication.translate("MainWindow", u"\u7d2f\u8ba1 0 \u884c | \u5339\u914d 0", None))
    # retranslateUi

