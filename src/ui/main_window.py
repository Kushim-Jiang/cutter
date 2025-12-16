from pathlib import Path
from typing import Optional

import cv2
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ocr.detector import detect_text_regions, filter_boxes
from ocr.merge import merge_boxes
from ocr_cropper.app_state import AppState
from ui.file_list import FileList
from ui.image_view import ImageView
from utils.deskew import auto_deskew
from utils.rules import apply_rules


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("汉字自动裁切工具")
        self.resize(1200, 800)

        self.state = AppState()

        self.file_list = FileList()
        self.file_list.currentItemChanged.connect(self.on_file_changed)

        open_btn = QPushButton("打开图片（批量）")
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
            sb.setRange(0, 2000)

        self.w_min.setValue(30)
        self.w_max.setValue(300)
        self.h_min.setValue(30)
        self.h_max.setValue(300)

        self.r_min.setRange(1, 300)
        self.r_max.setRange(1, 300)
        self.r_min.setValue(80)
        self.r_max.setValue(120)

        detect_btn = QPushButton("自动检测")
        detect_btn.clicked.connect(self.detect_current)

        merge_btn = QPushButton("框合并")
        merge_btn.clicked.connect(self.merge_current)

        apply_btn = QPushButton("应用规则")
        apply_btn.clicked.connect(self.apply_rules_current)

        export_btn = QPushButton("导出选中")
        export_btn.clicked.connect(self.export_current)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("宽度"))
        right_layout.addWidget(self.w_min)
        right_layout.addWidget(self.w_max)
        right_layout.addWidget(QLabel("高度"))
        right_layout.addWidget(self.h_min)
        right_layout.addWidget(self.h_max)
        right_layout.addWidget(QLabel("宽高比 (%)"))
        right_layout.addWidget(self.r_min)
        right_layout.addWidget(self.r_max)
        right_layout.addWidget(detect_btn)
        right_layout.addWidget(merge_btn)
        right_layout.addWidget(apply_btn)
        right_layout.addStretch()
        right_layout.addWidget(export_btn)

        root = QHBoxLayout()
        root.addLayout(left_layout, 1)
        root.addWidget(self.image_view, 4)
        root.addLayout(right_layout, 1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

    def open_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择图片", "", "Images (*.png *.jpg *.jpeg)")
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

    def detect_current(self) -> None:
        if not self.state.current:
            return

        w_range = (self.w_min.value(), self.w_max.value())
        h_range = (self.h_min.value(), self.h_max.value())
        ratio_range = (self.r_min.value() / 100.0, self.r_max.value() / 100.0)

        all_boxes = detect_text_regions(self.state.current)
        boxes = filter_boxes(all_boxes, w_range=w_range, h_range=h_range, ratio_range=ratio_range)
        self.state.images[self.state.current] = boxes

        self.image_view.load_image(self.state.current)
        self.image_view.load_boxes(boxes)

    def merge_current(self) -> None:
        if not self.state.current:
            return

        boxes = [item.box for item in self.image_view.box_items]
        merged = merge_boxes(boxes)

        self.state.images[self.state.current] = merged
        self.image_view.load_image(self.state.current)
        self.image_view.load_boxes(merged)

    def apply_rules_current(self) -> None:
        if not self.state.current:
            return

        boxes = [item.box for item in self.image_view.box_items]

        apply_rules(
            boxes,
            w=(self.w_min.value(), self.w_max.value()),
            h=(self.h_min.value(), self.h_max.value()),
            ratio=(self.r_min.value() / 100.0, self.r_max.value() / 100.0),
        )

        for item in self.image_view.box_items:
            item.update_style()

    def export_current(self) -> None:
        if not self.state.current:
            return

        out_dir_str = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not out_dir_str:
            return

        out_dir = Path(out_dir_str)

        img = cv2.imread(str(self.state.current))

        for i, item in enumerate(self.image_view.box_items):
            box = item.box
            if not box.selected:
                continue

            crop = img[box.y : box.y + box.h, box.x : box.x + box.w]
            cv2.imwrite(str(out_dir / f"crop_{i}.png"), crop)
