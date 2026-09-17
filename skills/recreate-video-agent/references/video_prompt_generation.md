# V6 Prompt入口

遵循local_prompt_pipeline.md。读取服务端时间轴并记录非阻塞警告，再按锁定Segment生成一次完整Prompt。shotIds/beatIds/utteranceIds提供覆盖记录，不允许合并或遗漏真实镜头。set-prompts必须核对窗口、板ID与各段蓝图引用。
