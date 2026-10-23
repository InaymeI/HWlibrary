"""Modal confirmation and progress for a lesson-wide answer submission."""
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..batch import LessonSender, lesson_plan


class LessonSendDialog(QDialog):
    def __init__(self, api, lesson_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Отправить ответы за весь урок")
        self.resize(650, 450)
        self.plan, skipped = lesson_plan(lesson_id)
        self.worker = None
        self.done_count = 0
        layout = QVBoxLayout(self)
        info = QLabel(
            f"К отправке: {len(self.plan)} заданий из домашних работ урока. "
            f"Пропущено без готового ответа: {skipped}.\n"
            "Используются только задания из SQLite. Незагруженные задания не включены.\n"
            "Отправка изменит результаты в личном кабинете. Уже отправленное отменить нельзя."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.confirm = QCheckBox("Подтверждаю отправку ответов за весь урок")
        layout.addWidget(self.confirm)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        row = QHBoxLayout()
        self.send = QPushButton("Отправить ответы")
        self.send.setEnabled(False)
        self.cancel = QPushButton("Закрыть")
        row.addWidget(self.send)
        row.addWidget(self.cancel)
        layout.addLayout(row)
        self.confirm.toggled.connect(lambda checked: self.send.setEnabled(checked and bool(self.plan)))
        self.send.clicked.connect(lambda: self.start(api))
        self.cancel.clicked.connect(self.reject)

    def start(self, api):
        if self.worker is not None:
            return
        self.send.setEnabled(False)
        self.confirm.setEnabled(False)
        self.cancel.setText("Остановить")
        self.worker = LessonSender(api, self.plan, self)
        self.worker.item_done.connect(self.result)
        self.worker.finished.connect(self.finished_sending)
        self.worker.start()

    def result(self, subject_id, test_id, message):
        self.done_count += 1
        self.log.appendPlainText(f"Домашка #{subject_id}, задание #{test_id}: {message}")

    def finished_sending(self):
        remaining = len(self.plan) - self.done_count
        self.log.appendPlainText(f"Обработано: {self.done_count}. Не отправлено: {remaining}.")
        self.cancel.setText("Закрыть")

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.cancel.setEnabled(False)
            self.log.appendPlainText("Остановка после текущего запроса…")
            self.worker.finished.connect(lambda: self.cancel.setEnabled(True))
            return
        super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            event.accept()
