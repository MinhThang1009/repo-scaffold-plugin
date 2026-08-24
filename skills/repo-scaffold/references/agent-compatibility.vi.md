# Tương thích agent

Repo Scaffold có một core theo chuẩn [Agent Skills](https://agentskills.io) tại
`skills/repo-scaffold/SKILL.md`. Core chứa quy trình scaffold, chính sách đầu
ra dự án tiếng Anh/tiếng Việt, assets và scripts kiểm tra. Không sao chép core
vào các adapter riêng của từng agent.

## Codex

Adapter cho Codex là `.codex-plugin/plugin.json`. Cài qua Codex marketplace
theo [tài liệu plugin chính thức](https://developers.openai.com/plugins/build/plugins),
rồi yêu cầu scaffold repository như bình thường.

Codex đọc hướng dẫn dự án từ `AGENTS.md` và áp dụng các tệp phù hợp từ root
của repository đến thư mục làm việc. `AGENTS.md` được tạo ra là điểm vào hướng
dẫn ở root theo ngôn ngữ đã chọn của scaffold; Codex vẫn có thể áp dụng thêm
hướng dẫn global hoặc nested phù hợp. Xem [tài liệu AGENTS.md chính
thức](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

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

Release asset chứa cả hai manifest trong thư mục `repo-scaffold/`. Có thể truyền
trực tiếp ZIP cho `claude --plugin-dir`, hoặc giải nén rồi truyền thư mục đã giải
nén.

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

## Hành vi multi-agent

Repo Scaffold không đóng gói custom subagent. Một skill dùng chung có thể được
gọi an toàn bởi agent chính hoặc subagent do host quản lý; host quyết định việc
phân công, concurrency, model và permission.

- Với Codex, repository đích có thể thêm custom agent theo phạm vi dự án tại
  `.codex/agents/<name>.toml`. Mỗi định nghĩa cần `name`, `description` và
  `developer_instructions`; chỉ thêm khi có một vai trò hẹp, thực sự cần thiết.
  Xem [tài liệu subagents chính thức của Codex](https://learn.chatgpt.com/docs/agent-configuration/subagents).
- Với Claude Code, repository đích có thể thêm project subagent tại
  `.claude/agents/<name>.md`. Một Claude Code plugin phân phối chỉ dùng thư mục
  `agents/` ở root của plugin khi nó thực sự đóng gói một agent chuyên biệt.
  Xem [tài liệu subagents chính thức của Claude Code](https://code.claude.com/docs/en/sub-agents).

Khi repository đích thêm custom agent, các agent đó phải dùng cùng contract
hướng dẫn `AGENTS.md` và `CLAUDE.md` đã tạo. Không sao chép quy trình scaffold
hoặc tạo biến thể ngôn ngữ riêng cho từng agent.

## Chính sách ngôn ngữ

Tài liệu tương thích agent có cả tiếng Anh và tiếng Việt. Với từng repository
đích, luôn chọn đúng một ngôn ngữ đầu ra dự án, `en` hoặc `vi`; lựa chọn này
ảnh hưởng file hướng tới người dùng của dự án, không ảnh hưởng adapter agent.
