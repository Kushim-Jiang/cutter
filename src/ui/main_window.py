from pathlib import Path
from typing import Optional

import cv2
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QKeyEvent, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from methods.deskew import auto_deskew
from methods.detector import detect_image, detect_selection
from models.box import Box
from models.state import AppState
from ui.box_item import BoxItem, sort_reading_order
from ui.file_list import FileList
from ui.image_view import ImageView


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoCUT")
        self.resize(1200, 800)
        self.acceptDrops()

        self.state = AppState()

        self.file_list = FileList()
        self.file_list.currentItemChanged.connect(self.on_file_changed)

        open_btn = QPushButton("Open Image")
        open_btn.clicked.connect(self.open_images)

        left_layout = QVBoxLayout()
        left_layout.addWidget(open_btn)
        left_layout.addWidget(self.file_list)

        self.image_view = ImageView()

        detect_btn = QPushButton("Detect Text Regions")
        detect_btn.clicked.connect(self.detect_current)
        self.image_view.detect.connect(self.detect_current)

        self.export_dir = QLineEdit(str(Path.cwd()))
        self.export_dir.setReadOnly(True)
        export_btn = QPushButton("Export Selected Regions")
        export_btn.clicked.connect(self.export_current)
        self.image_view.save.connect(self.export_current)

        right_layout = QVBoxLayout()

        self.rule_table = QTableWidget()
        self.rule_table.setColumnCount(4)
        self.rule_table.setHorizontalHeaderLabels(["w_min", "w_max", "h_min", "h_max"])
        self.rule_table.setRowCount(5)
        for row in range(5):
            for col in range(4):
                self.rule_table.setItem(row, col, QTableWidgetItem(""))
        self.rule_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        right_layout.addWidget(self.rule_table)
        right_layout.addWidget(detect_btn)
        right_layout.addStretch()
        right_layout.addWidget(self.export_dir)
        right_layout.addWidget(export_btn)

        root = QHBoxLayout()
        root.addLayout(left_layout, 1)
        root.addWidget(self.image_view, 4)
        root.addLayout(right_layout, 1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        self.status_pos = QLabel()
        self.status_sel = QLabel()
        self.status_zoom = QLabel()
        self.statusBar().addPermanentWidget(self.status_sel)
        self.statusBar().addPermanentWidget(self.status_pos)
        self.statusBar().addPermanentWidget(self.status_zoom)
        self.image_view.pos_str.connect(self.status_pos.setText)
        self.image_view.sel_str.connect(self.status_sel.setText)
        self.image_view.selection_finished.connect(self.on_selection_finished)
        self.image_view.zoom_changed.connect(self.status_zoom.setText)

    def open_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Open Images", "", "Images (*.png *.jpg *.jpeg)")
        if not files:
            return
        self.add_dirs(files)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        dirs = [url.toLocalFile() for url in urls if url.toLocalFile().endswith((".png", ".jpg", ".jpeg"))]
        if not dirs:
            return
        self.add_dirs(dirs)

    def add_dirs(self, dirs: list[str]) -> None:
        paths = [Path(p) for p in sorted(dirs)]
        self.file_list.load_files(paths)
        for p in paths:
            self.state.images[p] = None

        self.file_list.setCurrentRow(0)

    def on_file_changed(self, current: Optional[QListWidgetItem]) -> None:
        if not current:
            return

        if self.state.current:
            self.state.images[self.state.current] = [item.box for item in self.image_view.box_items]

        path = Path(current.text())
        self.state.current = path
        self.image_view.load_image(path)

        img = auto_deskew(cv2.imread(str(path)))
        temp_path = path.with_suffix(".deskew.png")
        (temp_path.parent.parent / "deskew").mkdir(parents=True, exist_ok=True)
        deskew_path = temp_path.parent.parent / "deskew" / temp_path.name
        self.export_dir.setText(str(deskew_path.parent.parent / "result"))

        cv2.imwrite(str(deskew_path), img)
        self.image_view.load_image(deskew_path)

        boxes = self.state.images.get(path)
        if boxes:
            self.image_view.load_boxes(boxes)

    def on_selection_finished(self, rect: QRect) -> None:
        if not self.state.current:
            return

        rect_box = Box(rect.left(), rect.top(), rect.width(), rect.height())
        if rect.width() < 10 or rect.height() < 10:
            return

        box = detect_selection(Image.open(self.state.current), rect_box)
        if box:
            box_item = BoxItem(box)
            self.image_view.scene().addItem(box_item)
            self.image_view.box_items.append(box_item)

    def detect_current(self) -> None:
        if not self.state.current:
            return

        boxes: list[Box] = []
        for row in range(self.rule_table.rowCount()):
            w_min = int(self.rule_table.item(row, 0).text() or 0)
            w_max = int(self.rule_table.item(row, 1).text() or 0)
            h_min = int(self.rule_table.item(row, 2).text() or 0)
            h_max = int(self.rule_table.item(row, 3).text() or 0)
            if w_min < w_max and h_min < h_max and max(w_max, h_max) > 0:
                boxes.extend(detect_image(self.state.current, W_RANGE=(w_min, w_max), H_RANGE=(h_min, h_max)))
        self.state.images[self.state.current] = boxes

        self.image_view.load_image(self.state.current)
        self.image_view.load_boxes(boxes)

    def export_current(self) -> None:
        if not self.state.current:
            return

        out_dir = Path(self.export_dir.text())
        out_dir.mkdir(parents=True, exist_ok=True)

        img = cv2.imread(str(self.state.current))
        ordered_boxes = sort_reading_order(self.image_view.box_items, img.shape[1])
        for i, item in enumerate(ordered_boxes, 1):
            box = item.box
            crop = img[box.y : box.y + box.h, box.x : box.x + box.w]
            cv2.imwrite(str(out_dir / f"{self.state.current.stem}_{i:03d}.png"), crop)

        # go to next image
        self.file_list.setCurrentRow(self.file_list.currentRow() + 1)
        self.detect_current()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.image_view.delete_selected_boxes()
        super().keyPressEvent(event)
