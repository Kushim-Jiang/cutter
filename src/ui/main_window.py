from pathlib import Path
from typing import Iterable, Optional

import cv2
from PIL import Image
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from methods.deskew import auto_deskew
from methods.detector import detect_image, detect_selection
from models.box import Box, coverage_deduplication
from models.state import AppState
from ui.box_item import BoxItem, sort_reading_order
from ui.image_view import ImageView


class TableWidget(QTableWidget):
    def __init__(self) -> None:
        super().__init__()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(["w_min", "w_max", "h_min", "h_max"])
        self.setRowCount(5)
        for row in range(self.rowCount()):
            for col in range(self.columnCount()):
                self.setItem(row, col, QTableWidgetItem(""))

        self.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        column_map = {0: 2, 1: 3}

        row, col = item.row(), item.column()
        text = item.text().strip()
        if (not text) or (col not in column_map):
            return
        target_item = self.item(row, column_map[col])
        if target_item is not None:
            self.blockSignals(True)
            target_item.setText(text)
            self.blockSignals(False)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.insertRow(self.rowCount())
            for col in range(self.columnCount()):
                self.setItem(self.rowCount() - 1, col, QTableWidgetItem(""))
        super().mouseDoubleClickEvent(event)


class FileList(QListWidget):
    def load_files(self, paths: Iterable[Path]) -> None:
        self.clear()
        for p in paths:
            self.addItem(str(p))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoCUT")
        self.resize(1200, 800)
        self.setAcceptDrops(True)

        self.state = AppState()

        self.file_list = FileList()
        self.file_list.currentItemChanged.connect(self.on_file_changed)

        # left layout
        open_btn = QPushButton("Open Image")
        open_btn.clicked.connect(self.open_images)
        left_layout = QVBoxLayout()
        left_layout.addWidget(open_btn)
        left_layout.addWidget(self.file_list)

        # center layout
        self.image_view = ImageView()

        detect_btn = QPushButton("Detect Text Regions")
        detect_btn.clicked.connect(self.detect_current)

        # right layout
        self.column_spin = QSpinBox()
        self.column_spin.setMinimum(1)
        self.column_spin.setValue(2)

        self.export_dir = QLineEdit(str(Path.cwd()))
        self.export_dir.setReadOnly(True)
        export_dir_btn = QPushButton("...")
        export_dir_btn.setFixedWidth(30)
        export_dir_btn.clicked.connect(self.select_export_directory)

        export_btn = QPushButton("Export Selected Regions")
        export_btn.clicked.connect(self.export_current)
        self.image_view.save.connect(self.export_current)

        self.rule_table = TableWidget()

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.rule_table)
        right_layout.addWidget(detect_btn)
        right_layout.addStretch()

        # export column layout
        export_column_layout = QHBoxLayout()
        export_column_layout.addWidget(QLabel("Columns:"))
        export_column_layout.addWidget(self.column_spin)
        right_layout.addLayout(export_column_layout)

        # export directory layout
        export_dir_layout = QHBoxLayout()
        export_dir_layout.addWidget(self.export_dir)
        export_dir_layout.addWidget(export_dir_btn)
        right_layout.addLayout(export_dir_layout)

        right_layout.addWidget(export_btn)

        # root layout
        root = QHBoxLayout()
        root.addLayout(left_layout, 1)
        root.addWidget(self.image_view, 4)
        root.addLayout(right_layout, 1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        # status bar
        self.status_pos = QLabel()
        self.status_sel = QLabel()
        self.status_zoom = QLabel()
        self.status_box = QLabel()
        self.statusBar().addPermanentWidget(self.status_box)
        self.statusBar().addPermanentWidget(self.status_sel)
        self.statusBar().addPermanentWidget(self.status_pos)
        self.statusBar().addPermanentWidget(self.status_zoom)

        self.image_view.pos_str.connect(self.status_pos.setText)
        self.image_view.sel_str.connect(self.status_sel.setText)
        self.image_view.box_str.connect(self.status_box.setText)
        self.image_view.selection_finished.connect(self.on_selection_finished)
        self.image_view.zoom_changed.connect(self.status_zoom.setText)

    def open_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Open Images", "", "Images (*.png *.jpg *.jpeg)")
        if files:
            self.add_files(files)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        files = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith((".png", ".jpg", ".jpeg"))
        ]
        if files:
            self.add_files(files)

    def add_files(self, files: list[str]) -> None:
        paths = [Path(f) for f in sorted(files)]
        self.file_list.load_files(paths)
        for p in paths:
            self.state.images[p] = None
        self.file_list.setCurrentRow(0)

    def select_export_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Export Directory")
        if directory:
            self.export_dir.setText(directory)

    def on_file_changed(self, current: Optional[QListWidgetItem]) -> None:
        if not current:
            return

        if self.state.current:
            self.state.images[self.state.current] = [item.box for item in self.image_view.box_items]

        path = Path(current.text())
        img_cv = cv2.imread(str(path))
        deskew_img = auto_deskew(img_cv)

        deskew_dir = path.parent.parent / "deskew"
        deskew_dir.mkdir(exist_ok=True, parents=True)
        deskew_path = deskew_dir / f"{path.stem}.deskew.png"
        cv2.imwrite(str(deskew_path), deskew_img)
        self.state.current = deskew_path
        self.image_view.load_image(self.state.current)

        default_export_dir = deskew_dir.parent / "result"
        self.export_dir.setText(str(default_export_dir))

        boxes = self.state.images.get(path)
        if boxes:
            self.image_view.load_boxes(boxes)

    def on_selection_finished(self, rect: QRect) -> None:
        if not self.state.current:
            return

        if rect.width() < 10 or rect.height() < 10:
            return

        rect_box = Box(rect.left(), rect.top(), rect.width(), rect.height())
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
            try:
                w_min = int(self.rule_table.item(row, 0).text() or 0)
                w_max = int(self.rule_table.item(row, 1).text() or 0)
                h_min = int(self.rule_table.item(row, 2).text() or 0)
                h_max = int(self.rule_table.item(row, 3).text() or 0)
            except ValueError:
                continue

            if w_min < w_max and h_min < h_max and max(w_max, h_max) > 0:
                boxes.extend(detect_image(self.state.current, W_RANGE=(w_min, w_max), H_RANGE=(h_min, h_max)))

        final_boxes = coverage_deduplication(boxes)
        self.state.images[self.state.current] = final_boxes
        self.image_view.load_image(self.state.current)
        self.image_view.load_boxes(final_boxes)

    def export_current(self) -> None:
        if not self.state.current:
            return

        column_count = self.column_spin.value()
        out_dir = Path(self.export_dir.text())
        out_dir.mkdir(parents=True, exist_ok=True)

        img = cv2.imread(str(self.state.current))
        ordered_boxes = sort_reading_order(self.image_view.box_items, img.shape[1], column_count)

        for idx, box_item in enumerate(ordered_boxes, 1):
            box = box_item.box
            cropped = img[box.y : box.y + box.h, box.x : box.x + box.w]
            out_path = out_dir / f"{self.state.current.stem.removesuffix('.deskew')}_{idx:03d}.png"
            cv2.imwrite(str(out_path), cropped)

        # go to next image
        next_row = self.file_list.currentRow() + 1
        if next_row < self.file_list.count():
            self.file_list.setCurrentRow(next_row)
            self.detect_current()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.image_view.delete_selected_boxes()
        super().keyPressEvent(event)
