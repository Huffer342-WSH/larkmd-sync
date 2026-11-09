# 架构与设计文档

开发从 [根 AGENTS.md](../AGENTS.md) 开始；安装和命令示例见 [项目 README](../README.md)。

| 文档 | 职责 |
| --- | --- |
| [架构与代码地图](架构.md) | 组件关系、公共接口、Skill 生命周期与实现定位 |
| [同步设计](同步设计.md) | 快照、plan/apply、文档映射和资源处理 |
| [Markdown 格式](Markdown格式.md) | 本地表示、格式转换与标题编号 |
| [原生画板同步](原生画板同步.md) | SVG、原生节点、同步基准与冲突处理 |
| [字体颜色](字体颜色.md) | 颜色语法、色板映射与保真边界 |

机器接口的命令和错误码统一维护在随包发布的 [CLI 参考](../src/larkmd_sync/skills/larkmd-sync/references/CLI.md)；Agent 操作流程见 [内置 Skill](../src/larkmd_sync/skills/larkmd-sync/SKILL.md)。
