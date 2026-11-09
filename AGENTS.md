# larkmd-sync 开发入口

本项目提供公共 Python API、CLI 和随包发布的 Agent Skill。开始修改前检查 Git 状态，保留已有改动。

## 按任务阅读

- 首次开发或调整模块职责：读 [架构与代码地图](docs/架构.md)，确认入口、依赖和副作用边界。
- 修改拉取、推送、计划或 Wiki 映射：读 [同步设计](docs/同步设计.md)，再定位 `sync.py` 和相关测试。
- 修改 Markdown 转换：读 [格式与往返约束](docs/Markdown格式.md)；颜色和画板继续沿该页专题链接阅读。
- 修改命令、公共 API、Skill 或发布流程：读 [架构中的公共契约](docs/架构.md) 和 [CLI 契约](src/larkmd_sync/skills/larkmd-sync/references/CLI.md)。
- 查找设计专题：从 [文档索引](docs/README.md) 进入对应模块说明。

修改子目录前检查沿途是否存在局部 AGENTS.md，有则显式读取。本项目当前没有局部指引，不要求预读全部文档。

## 必须保持

- CLI 调用公共 API；飞书访问统一通过 `lark_cli.py` 调用官方 CLI，不在 Skill 中复制业务逻辑。
- 同步先 plan 后 apply，保留过期计划、阻断警告、文件路径和画板冲突校验。失败不得伪造成功基准；多步远端写入不是事务。
- Skill 唯一来源是 `src/larkmd_sync/skills/`；安装包不自动安装 Skill。不要覆盖来源未知的用户文件。
- 不把真实测试文档、令牌或下载素材加入发行包。线上写入只使用任务明确授权的目标。

行为变化时更新所属专题和相关测试；新增模块补架构定位，移动或删除文档修复入链。保持有效开发文档从本入口可达。docs/ 只维护当前架构和设计，不加入临时试验、测试报告或同步样本文档。

## 开发验证

在项目根目录运行 `uv run --group dev python -m pytest -q` 和 `uv run --group dev ruff check src tests scripts`。优先运行相关测试，结构和公共契约变更运行完整离线回归；简单转发不重复测试，保真、路径安全、冲突和失败恢复必须保留覆盖。

打包使用 `uv build --out-dir <空产物目录>`，随后用 `uv run python scripts/check_artifacts.py <wheel路径> <sdist路径>` 检查完整 Skill 资源。在临时环境安装 wheel、从仓库外验证 Skill 读取与显式安装，不能以源码导入代替构建验证。

离线测试不代表线上同步成功。真实写入只使用授权目标，执行 plan/apply 后读回；失败先核对远端状态，不盲目重试。临时测试下载保存在已忽略的 testdata/，不纳入架构文档。
