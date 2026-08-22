# Tương thích agent

Repo Scaffold có một core theo chuẩn [Agent Skills](https://agentskills.io) tại
`skills/repo-scaffold/SKILL.md`. Core chứa quy trình scaffold, chính sách đầu
ra dự án tiếng Anh/tiếng Việt, assets và scripts kiểm tra. Không sao chép core
vào các adapter riêng của từng agent.

## Codex

Adapter cho Codex là `.codex-plugin/plugin.json`. Cài qua Codex marketplace
theo [tài liệu plugin chính thức](https://developers.openai.com/plugins/build/plugins),
rồi yêu cầu scaffold repository như bình thường.

## Claude Code

Adapter cho Claude Code là `.claude-plugin/plugin.json`. Claude Code tự phát
hiện thư mục `skills/` chuẩn trong plugin, nên dùng chung core skill mà không
cần wrapper sao chép. Kiểm tra local từ root của repository:

```bash
claude plugin validate --strict .
claude --plugin-dir .
```

Trong phiên kết quả, gọi `/repo-scaffold:repo-scaffold` hoặc yêu cầu Claude
scaffold repository. Xem [plugin guide](https://code.claude.com/docs/en/plugins)
và [skills guide](https://code.claude.com/docs/en/skills) chính thức của Claude
Code để biết layout và định dạng `SKILL.md` dùng chung.

Release asset chứa cả hai manifest trong thư mục `repo-scaffold/`. Hãy giải nén
archive rồi truyền thư mục đã giải nén cho `claude --plugin-dir`.

## Agent khác

Chỉ dùng trực tiếp core với agent hỗ trợ chuẩn Agent Skills. Hãy trỏ cơ chế
skill discovery của agent vào `skills/`, hoặc import
`skills/repo-scaffold/SKILL.md` bằng cơ chế được agent đó công bố. Repository
này không tuyên bố hỗ trợ cài đặt native cho agent chưa được xác minh và không
tự tạo định dạng cấu hình riêng cho nó.

Hướng dẫn của host luôn có hiệu lực cao hơn. Codex có thể dùng `AGENTS.md`;
Claude Code đọc `CLAUDE.md`, không đọc trực tiếp `AGENTS.md`. Khi repository
đích cần dùng chung hướng dẫn, dùng `CLAUDE.md` chứa `@AGENTS.md`. Scaffold
cung cấp đúng adapter đó tại `assets/CLAUDE.md`. Xem [tài liệu memory của
Claude Code](https://code.claude.com/docs/en/memory).

## Chính sách ngôn ngữ

Tài liệu tương thích agent có cả tiếng Anh và tiếng Việt. Với từng repository
đích, luôn chọn đúng một ngôn ngữ đầu ra dự án, `en` hoặc `vi`; lựa chọn này
ảnh hưởng file hướng tới người dùng của dự án, không ảnh hưởng adapter agent.
