# Segment Storyboard交接

`prepare_prompt_handoff.py`直接复用已登记最终板路径与字节。当前Planner V1新任务为3×3、9个anchors，并建立全局→局部时间映射；布局由客户端根据anchors数量派生。不要调用旧`segment_storyboards.py`重新拆格合成。代码仍可读取历史4×4任务，仅用于兼容旧Manifest。
