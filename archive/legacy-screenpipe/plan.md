# Plan: MeetingHelper 部署教程更新

> Last checkpoint: 2026-04-17 10:42
> Branch: main
> Status: in-progress

## Context

MeetingHelper 是一个本地 AI 会议助手，包含字幕覆盖层和 Screenpipe 后台服务两层架构。当前正在对 `tutorial.md` 进行大幅更新（230 行新增、127 行删除），改进部署教程的内容和结构。该更改尚未提交。

## Done

- [x] MeetingHelper 项目初始发布 (initial release)
- [x] 添加详细部署教程 (`tutorial.md`)
- [x] 新增 Screenpipe 快速启动脚本
- [x] 修复 Homebrew Screenpipe 弃用问题和模型问题
- [x] 添加 Homebrew Screenpipe 卸载说明
- [x] 对 `tutorial.md` 进行大幅重写/更新（已修改，未提交）

## Next

- [ ] 审查 `tutorial.md` 的修改内容，确认无误后提交
- [ ] 验证教程中的命令和路径是否正确
- [ ] 考虑是否需要更新 CLAUDE.md 以匹配教程变更

## Backlog

- [ ] 进一步完善各 ASR 后端的使用说明
- [ ] 添加常见问题排查文档

## Open Questions

- `tutorial.md` 的大幅修改是否已完成，还是仍在编辑中？

## Why

教程是用户上手的入口，部署教程需要随项目演进持续更新。当前修改幅度较大（重写了约 60% 的内容），说明教程结构或内容有较大调整需求。

## Notes

- 未提交的修改仅涉及 `tutorial.md`，其余代码无变更
- 项目共 7 次提交，全部在 main 分支上
