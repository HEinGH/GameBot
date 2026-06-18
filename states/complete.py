import time
import logging

from core.fsm import BaseState

logger = logging.getLogger(__name__)


class CompleteState(BaseState):
    def __init__(self, controller):
        self.controller = controller

    def enter(self, blackboard):
        super().enter(blackboard)
        self.controller.release_all()
        logger.info("全部角色执行完成，停止运行")
        blackboard["running"] = False

    def update(self, blackboard):
        pass
