# -*- coding: utf-8 -*-
import ast, re
from pathlib import Path

WIN = Path(r'G:\Python\jcspy\Super_ADB\Super_ADB_Win')

# 不能改的函数名（Qt回调/标准接口/外部依赖）
PROTECTED = {
    'eventFilter','mousePressEvent','mouseReleaseEvent','mouseMoveEvent',
    'paintEvent','resizeEvent','closeEvent','showEvent','hideEvent',
    'keyPressEvent','keyReleaseEvent','wheelEvent','dragEnterEvent',
    'dragMoveEvent','dropEvent','contextMenuEvent','focusInEvent',
    'focusOutEvent','changeEvent','timerEvent','run','setupUi',
    'retranslateUi','apply_theme','accept','reject','exec','open',
    'show','hide','close','update','repaint','raise_','activateWindow',
    'setStyleSheet','setWindowFlags','setAttribute','setGeometry',
    'saveGeometry','restoreGeometry','winId','windowHandle','style',
    'unpolish','polish','itemData','addItem','clear','findData',
    'setCurrentIndex','currentIndex','blockSignals','setText','text',
    'isVisible','destroyed','connect','start','stop','isRunning',
    'quit','wait','setSingleShot','startTimer','killTimer','addAction',
    'setMenu','setCheckable','setChecked','isChecked','trigger',
    'setParent','setLayout','addWidget','setContentsMargins','setSpacing',
    'setMinimumSize','setMaximumSize','setFixedSize','resize','move',
    'pos','width','height','size','geometry','frameGeometry',
    'normalGeometry','mapToGlobal','mapFromGlobal','setCursor','cursor',
    'setMouseTracking','setAcceptDrops','setToolTip','setStatusTip',
    'setWhatsThis','setAccessibleName','setObjectName','objectName',
    'setProperty','property','installEventFilter','removeEventFilter',
    'sender','children','findChild','findChildren','setWindowTitle',
    'windowTitle','setWindowIcon','windowIcon','setWindowModality',
    'setResult','result','done','setModal','isModal','setSizeGripEnabled',
    'setOrientation','setMode','setViewMode','setIconSize','setGridSize',
    'setMovement','setResizeMode','setSelectionMode','setTabKeyNavigation',
    'setAlternatingRowColors','setSortingEnabled','header','setHeaderHidden',
    'setRootIsDecorated','setItemsExpandable','setAnimated',
    'setExpandsOnDoubleClick','setUniformRowHeights','setAllColumnsShowFocus',
    'setColumnHidden','setColumnWidth','resizeColumnToContents','setWordWrap',
    'setFrameShape','setFrameShadow','setLineWidth','setMidLineWidth',
    'setAlignment','setIndent','setMargin','setPadding','setScaledContents',
    'setPixmap','pixmap','setMovie','movie','setBuffer','setFormat',
    'setReadOnly','setEditable','setInsertPolicy','setCompleter',
    'setMaxCount','setMaxVisibleItems','setMinimumContentsLength',
    'setSizeAdjustPolicy','setDuplicatesEnabled','setFrame','setIcon',
    'icon','setFlat','setDefault','setAutoDefault','setPopupMode',
    'setArrowType','setNotExclusive','setAutoRepeat','setAutoRepeatInterval',
    'setAutoRepeatDelay','setCheckState','checkState','setTristate',
    'setNum','setDigitCount','setSegmentStyle','setStepType','setAccelerated',
    'setAutoFillBackground','setPalette','palette','setFont','font',
    'setFocus','focusWidget','setFocusPolicy','focusPolicy',
    'setContextMenuPolicy','contextMenuPolicy','setLayoutDirection',
    'layoutDirection','setLocale','locale','setInputMethodHints',
    'inputMethodHints','setSizePolicy','sizePolicy','setMinimumWidth',
    'setMinimumHeight','setMaximumWidth','setMaximumHeight','setBaseSize',
    'setFixedWidth','setFixedHeight','adjustSize','sizeHint',
    'minimumSizeHint','minimumSize','maximumSize','setSizeIncrement',
    'sizeIncrement','setWindowOpacity','windowOpacity','setWindowState',
    'windowState','setWindowRole','windowRole','setWindowFilePath',
    'windowFilePath','setWindowModified','isWindowModified','setOrientation',
    'setTabPosition','tabPosition','setDocumentMode','documentMode',
    'setTabsClosable','tabsClosable','setMovable','isMovable',
    'setElideMode','elideMode','setUsesScrollButtons','usesScrollButtons',
    'setExpanding','expanding','setCurrentWidget','currentWidget',
    'addTab','insertTab','removeTab','indexOf','widget','count',
    'setTabText','tabText','setTabIcon','tabIcon','setTabToolTip',
    'tabToolTip','setTabWhatsThis','tabWhatsThis','setTabEnabled',
    'isTabEnabled','setTabVisible','isTabVisible','setTabColor','tabBar',
    'tabBarAutoHide','setTabBarAutoHide','isTabBarAutoHide',
    'setCornerWidget','cornerWidget','removeAction','insertAction',
    'actions','customContextMenuRequested','setToolTipDuration',
    'toolTipDuration','setAccessibleDescription','accessibleDescription',
    'autoFillBackground','setLayout','layout','parent',
    'dynamicPropertyNames','event','childEvent','customEvent',
    'connectNotify','disconnectNotify','senderSignalIndex','receivers',
    'isSignalConnected','signalsBlocked','dumpObjectTree','dumpObjectInfo',
    'setChildren','metaObject','inherits','deleteLater','objectNameChanged',
    'windowTitleChanged','windowIconChanged','windowIconTextChanged',
    'setScreen','screen','effectiveWinId','create','destroy',
    'setRenderHints','render','grab','renderPixmap','setGraphicsEffect',
    'graphicsEffect','setGraphicsProxyWidget','graphicsProxyWidget',
    'setFocusProxy','focusProxy','nextInFocusChain','previousInFocusChain',
    'focusNextChild','focusPreviousChild','focusNextPrevChild','setTabOrder',
    'setWidgetAttribute','testAttribute','setWindowFlag','setVisible',
    'setHidden','setEnabled','isEnabled','setDisabled','styleSheet',
    'setStyle','x','y','rect','childrenRect','contentsRect','visibleRegion',
    'mapToParent','mapFromParent','mapTo','mapFrom','getContentsMargins',
    'contentsMargins','setStretch','setStretchFactor','addLayout','addItem',
    'insertWidget','insertLayout','insertItem','removeWidget','removeItem',
    'setDirection','direction','setPlainText','plainText','setHtml','html',
    'toHtml','toPlainText','setDocument','document','isReadOnly',
    'setUndoRedoEnabled','isUndoRedoEnabled','undo','redo','selectAll',
    'cut','copy','paste','canPaste','setSelection','selectionStart',
    'selectionEnd','selectedText','setCursorPosition','cursorPosition',
    'insert','insertPlainText','insertHtml','append','setLineWrapMode',
    'lineWrapMode','setLineWrapColumnOrWidth','lineWrapColumnOrWidth',
    'setTabStopDistance','tabStopDistance','setTabStopWidth','tabStopWidth',
    'setCursorWidth','cursorWidth','setTextInteractionFlags',
    'textInteractionFlags','setOverwriteMode','overwriteMode',
    'setAcceptRichText','acceptRichText','setExtraSelections',
    'extraSelections','setDocumentTitle','documentTitle',
    'setPlaceholderText','placeholderText','setMaxLength','maxLength',
    'setEchoMode','echoMode','setValidator','validator','setInputMask',
    'inputMask','setTextMargins','getTextMargins','frame','frameShape',
    'frameShadow','lineWidth','midLineWidth','frameWidth','setFrameRect',
    'frameRect','hasScaledContents','setFormat','format','setOpenExternalLinks',
    'openExternalLinks','setTextFormat','textFormat','setPicture','picture',
    'toPlainText','exec_','lower','showMinimized','showMaximized',
    'showFullScreen','showNormal','isVisibleTo','isHidden','isMinimized',
    'isMaximized','isFullScreen','isActiveWindow','setDisabled',
    'styleSheet','setStyle','testAttribute','hasMouseTracking',
    'clearFocus','setToolTipDuration','toolTipDuration','setLocale',
    'locale','setInputMethodHints','inputMethodHints','setSizePolicy',
    'sizePolicy','setBaseSize','baseSize','setSizeIncrement','sizeIncrement',
    'setWindowState','windowState','setWindowRole','windowRole',
    'setWindowFilePath','windowFilePath','isWindowModified','setModal',
    'isModal','setResult','result','done','accept','reject','open',
    'exec','exec_','show','hide','close','isVisible','isVisibleTo',
    'isHidden','isMinimized','isMaximized','isFullScreen','isActiveWindow',
    'activateWindow','raise_','lower','showMinimized','showMaximized',
    'showFullScreen','showNormal','setVisible','setHidden','setEnabled',
    'isEnabled','setDisabled','setStyleSheet','styleSheet','setStyle',
    'style','unpolish','polish','setAttribute','testAttribute','setCursor',
    'cursor','setMouseTracking','hasMouseTracking','setFocus','setFocusPolicy',
    'focusPolicy','setContextMenuPolicy','contextMenuPolicy','setAcceptDrops',
    'acceptDrops','setToolTip','toolTip','setWhatsThis','whatsThis',
    'setAccessibleName','accessibleName','setLayoutDirection','layoutDirection',
    'setAutoFillBackground','autoFillBackground','setPalette','palette',
    'setFont','font','setLayout','layout','setParent','parent',
    'setObjectName','objectName','setProperty','property','installEventFilter',
    'removeEventFilter','event','eventFilter','timerEvent','childEvent',
    'customEvent','sender','blockSignals','signalsBlocked','findChild',
    'findChildren','children','deleteLater','destroyed','winId',
    'windowHandle','grab','render','setGraphicsEffect','graphicsEffect',
}

all_funcs = {}
for p in sorted(WIN.rglob('*.py')):
    if '__pycache__' in p.parts: continue
    if p.name == 'png_rc.py': continue
    try:
        tree = ast.parse(p.read_text(encoding='utf-8'))
    except: continue
    rel = str(p.relative_to(WIN))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            name = node.name
            if re.search(r'[\u4e00-\u9fa5]', name): continue
            if name in PROTECTED: continue
            if name.startswith('__') and name.endswith('__'): continue
            if name not in all_funcs:
                all_funcs[name] = []
            all_funcs[name].append(rel)

print(f'需要改名的英文函数: {len(all_funcs)} 个')
for name in sorted(all_funcs.keys()):
    files = ', '.join(all_funcs[name][:2])
    print(f'  {name} ({len(all_funcs[name])}处) - {files}')
