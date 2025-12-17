from pathlib import Path
from typing import Optional

import cv2
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtGui import QKeyEvent, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models.box import Box
from ocr.detector import detect_text_regions, refine_box_from_selection
from ocr_cropper.app_state import AppState
from ui.box_item import BoxItem, sort_reading_order
from ui.file_list import FileList
from ui.image_view import ImageView
from utils.deskew import auto_deskew
from utils.rules import apply_rules


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoCUT")
        self.resize(1200, 800)

        self.state = AppState()

        self.file_list = FileList()
        self.file_list.currentItemChanged.connect(self.on_file_changed)

        open_btn = QPushButton("Open Image")
        open_btn.clicked.connect(self.open_images)

        left_layout = QVBoxLayout()
        left_layout.addWidget(open_btn)
        left_layout.addWidget(self.file_list)

        self.image_view = ImageView()

        self.w_min = QSpinBox()
        self.w_max = QSpinBox()
        self.h_min = QSpinBox()
        self.h_max = QSpinBox()
        self.r_min = QSpinBox()
        self.r_max = QSpinBox()

        for sb in (self.w_min, self.w_max, self.h_min, self.h_max):
            sb.setRange(0, 20000)

        self.w_min.setValue(30)
        self.w_max.setValue(300)
        self.h_min.setValue(30)
        self.h_max.setValue(300)

        detect_btn = QPushButton("Detect Text Regions")
        detect_btn.clicked.connect(self.detect_current)

        apply_btn = QPushButton("Apply Rules")
        apply_btn.clicked.connect(self.apply_rules_current)

        self.export_dir = QLineEdit(str(Path.cwd()))
        export_btn = QPushButton("Export Selected Regions")
        export_btn.clicked.connect(self.export_current)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Width Range"))
        right_layout.addWidget(self.w_min)
        right_layout.addWidget(self.w_max)
        right_layout.addWidget(QLabel("Height Range"))
        right_layout.addWidget(self.h_min)
        right_layout.addWidget(self.h_max)
        right_layout.addWidget(detect_btn)
        right_layout.addWidget(apply_btn)
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

        paths = [Path(p) for p in files]

        self.file_list.load_files(paths)
        for p in paths:
            img = cv2.imread(str(p))
            if img is None:
                continue
            img = auto_deskew(img)
            temp_path = p.with_suffix(".deskew.png")

            (temp_path.parent.parent / "deskew").mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(temp_path.parent.parent / "deskew" / temp_path.name), img)

            self.state.images[temp_path] = None

        self.file_list.setCurrentRow(0)

    def on_file_changed(
        self,
        current: Optional[QListWidgetItem],
        previous: Optional[QListWidgetItem],
    ) -> None:
        if not current:
            return

        if self.state.current:
            self.state.images[self.state.current] = [item.box for item in self.image_view.box_items]

        path = Path(current.text())
        self.state.current = path

        self.image_view.load_image(path)

        boxes = self.state.images.get(path)
        if boxes:
            self.image_view.load_boxes(boxes)

    def on_selection_finished(self, rect: QRect) -> None:
        if not self.state.current:
            return

        rect_box = Box(rect.left(), rect.top(), rect.width(), rect.height())
        if rect.width() < self.w_min.value() or rect.height() < self.h_min.value():
            return

        box = refine_box_from_selection(Image.open(self.state.current), rect_box)
        if box:
            box_item = BoxItem(box)
            self.image_view.scene().addItem(box_item)
            self.image_view.box_items.append(box_item)

    def detect_current(self) -> None:
        if not self.state.current:
            return

        w_range = (self.w_min.value(), self.w_max.value())
        h_range = (self.h_min.value(), self.h_max.value())

        boxes = detect_text_regions(self.state.current, w_range, h_range)
        self.state.images[self.state.current] = boxes

        self.image_view.load_image(self.state.current)
        self.image_view.load_boxes(boxes)

    def apply_rules_current(self) -> None:
        if not self.state.current:
            return

        boxes = [item.box for item in self.image_view.box_items]

        apply_rules(
            boxes,
            w=(self.w_min.value(), self.w_max.value()),
            h=(self.h_min.value(), self.h_max.value()),
        )

        for item in self.image_view.box_items:
            item.update_style()

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

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.image_view.delete_selected_boxes()
        super().keyPressEvent(event)
