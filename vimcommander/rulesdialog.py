""":rules preview: every file the folder rules would move, copy or trash, with a
checkbox each. Nothing happens until Apply."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .rules import Step
from .sizing import fit_window


class RulesDialog(QDialog):
    def __init__(self, steps: list[Step], parent: QWidget):
        super().__init__(parent)
        self.steps = steps
        self.chosen: list[Step] = []
        self.setWindowTitle("Folder rules")
        fit_window(self, 1000, 600)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Action", "To", "Rule"])
        self.tree.setRootIsDecorated(False)
        for i, step in enumerate(steps):
            item = QTreeWidgetItem([step.path, step.action, step.to, step.rule])
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            item.setToolTip(0, step.path)
            self.tree.addTopLevelItem(item)
        for col in range(4):
            self.tree.resizeColumnToContents(col)
        self.tree.installEventFilter(self)
        info = QLabel(f"{len(steps)} file(s) would be handled. Untick what to leave alone "
                      "(Space toggles the selected rows); Apply does the rest. u undoes it.")
        info.setWordWrap(True)
        buttons = QDialogButtonBox()
        buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.tree, 1)
        layout.addWidget(buttons)

    def eventFilter(self, obj, event):
        if obj is self.tree and event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Space:
            items = self.tree.selectedItems() or ([self.tree.currentItem()] if self.tree.currentItem() else [])
            on = any(i.checkState(0) != Qt.CheckState.Checked for i in items)
            for item in items:
                item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            return True
        return super().eventFilter(obj, event)

    def _apply(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                self.chosen.append(self.steps[item.data(0, Qt.ItemDataRole.UserRole)])
        self.accept()


def group_steps(steps: list[Step]) -> list[tuple[str, str, list[str]]]:
    """(action, destination, paths) per job to run."""
    groups: dict[tuple[str, str], list[str]] = {}
    for s in steps:
        groups.setdefault((s.action, s.to), []).append(s.path)
    return [(action, to, paths) for (action, to), paths in groups.items()]

