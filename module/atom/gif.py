# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import types

from module.atom.image import RuleImage
from module.image.rpc import get_image_client



class RuleGif:
    # 大部分实现同RuleImage 的接口

    @property
    def name(self) -> str:
        return self.appear_target.name

    @staticmethod
    def attach_to(image_obj: RuleImage, targets: list[RuleImage]) -> None:
        """把 GIF 的多帧匹配行为原地挂载到一个 RuleImage 实例上。

        与 `replace_img` 的原地换属性同思路：不换对象、不换引用，
        持有该对象引用的 Page/寻路会立即生效。

        比上游实现多同步了 `file`：本库靠在 `I_CHECK_MAIN.file` 上反查
        当前激活的庭院皮肤（`CostumeBase._infer_active_main_type`），
        不换 `file` 会让多帧皮肤无法被反查出来。

        :param image_obj: 被挂载的 RuleImage 实例
        :param targets: 多帧 RuleImage, 顺序即匹配优先级
        """
        # GIF状态
        image_obj.targets = targets
        image_obj.roi_front = [0, 0, 0, 0]
        image_obj.roi_back = targets[0].roi_back
        image_obj.appear_target = targets[0]
        image_obj.name = targets[0].name
        image_obj.file = targets[0].file
        image_obj._match_init = False
        # 方法级替换: 把 RuleGif 的所有公开方法绑定到该实例上
        for name, attr in RuleGif.__dict__.items():
            if name.startswith('_'):
                continue
            if isinstance(attr, types.FunctionType):
                setattr(image_obj, name, types.MethodType(attr, image_obj))

    @staticmethod
    def detach_from(image_obj: RuleImage) -> None:
        """撤销 `attach_to` 的原地挂载，恢复 RuleImage 原生匹配行为。

        多帧皮肤切回单帧皮肤时必须调用：实例上残留的 GIF 方法会继续按
        旧的多帧目标匹配，而 `replace_img` 只还原字段、不摘方法。
        对普通的 RuleImage 调用是安全的空操作。
        """
        for name, attr in RuleGif.__dict__.items():
            if name.startswith('_'):
                continue
            if isinstance(attr, types.FunctionType):
                image_obj.__dict__.pop(name, None)
        for name in ('targets', 'appear_target'):
            image_obj.__dict__.pop(name, None)
        # 丢掉可能指向旧帧的缓存模板，强制按当前 file 重新加载
        image_obj._image = None

    def __init__(self, targets: list[RuleImage]):
        self.targets = targets
        self.roi_front: list = [0, 0, 0, 0]
        self.appear_target = targets[0]

    def pre_process(self, image):
        return image


    def search(self, image, roi: list = None, threshold: float = None, frame_id: str = None) -> tuple:
        """

        :param image:
        :param roi:
        :param threshold:
        :return: bool
        第一项是否为出现, 第二项为匹配的RuleImage
        """
        processed_image = self.pre_process(image)
        #
        threshold = self.targets[0].threshold if threshold is None else threshold
        roi = self.targets[0].roi_back if roi is None else roi
        for target in self.targets:
            target.roi_back = roi

        payloads = [target.to_service_payload() for target in self.targets]
        active_frame_id = frame_id if processed_image is image else None
        results = get_image_client().match_many(
            rules_data=payloads,
            image=processed_image,
            frame_id=active_frame_id,
            threshold=threshold,
        )
        for target, result in zip(self.targets, results):
            if target._apply_match_result(result):
                self.roi_front = target.roi_front
                self.appear_target = target
                return True, target
        return False, None

    def match(self, image, threshold: float = None, frame_id: str = None) -> bool:
        return self.search(image, threshold=threshold, frame_id=frame_id)[0]


    def coord(self) -> tuple:
        return self.appear_target.coord()

    def front_center(self) -> tuple:
        x, y, w, h = self.roi_front
        return int(x + w//2), int(y + h//2)
