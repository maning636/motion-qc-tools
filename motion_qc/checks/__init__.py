"""检查项注册表。顺序就是报告里的展示顺序。"""

CHECKS = ("smoke", "overflow", "jscheck", "compose")

DESCRIPTIONS = {
    "smoke": "运行时烟测：组件真跑起来了吗（异常 / 时间轴 / 时长 / 是不是几乎不动）",
    "overflow": "落定期出框扫描：动画结束后还有东西停在画布外吗",
    "jscheck": "JS 语法检查：内联 <script> 能不能被解析（node --check）",
    "compose": "compose/2 编排核对：契约、图层运动包络出框、时段重叠、裸底时长",
}

__all__ = ["CHECKS", "DESCRIPTIONS"]
