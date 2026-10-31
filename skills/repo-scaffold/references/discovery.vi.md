**Ngôn ngữ:** [English](discovery.md) | [Tiếng Việt](discovery.vi.md)

# Discovery và an toàn đường dẫn

Đọc tài liệu này trong pha khảo sát.

Trong working tree, xác định repository root tuyệt đối bằng
`git rev-parse --show-toplevel`; ngoài working tree, dùng thư mục target tuyệt
đối đã được người dùng xác nhận. Neo mọi path vào root đó. Trước khi đọc, duyệt,
tạo hoặc ghi path, normalize nó, yêu cầu nằm trong descendant an toàn về biên
và từ chối mọi thành phần symbolic link, junction, mount point hoặc reparse
point. Lặp lại kiểm tra thành phần ngay trước khi mở path vừa tạo. Không follow
link do repository kiểm soát trừ khi người dùng xác nhận rõ external target đã
resolve và mở rộng phạm vi filesystem.

Lập overwrite inventory từ file tracked, untracked và ignored, rồi kiểm tra tồn
tại theo literal ngay trước từng lần ghi. Tìm manifest riêng, loại dependency/
build tree như `node_modules`, `.venv`, `vendor`, `dist`, `build`, `target` trừ
khi người dùng xác nhận đó là first-party workspace.

Với GitHub.com, phân tích mọi fetch remote như dữ liệu và thu các ứng viên
`OWNER/REPO` khác nhau. Nếu có hơn một ứng viên, trình bày mapping và hỏi; không
ưu tiên `origin`, `upstream`, `GH_REPO` hay repository mặc định của GitHub CLI.
Truyền tường minh `github.com/OWNER/REPO` cùng `--hostname github.com` cho mọi
lệnh GitHub CLI và yêu cầu `nameWithOwner` trả về khớp. Fork cần xác nhận này vì
GitHub CLI có thể mặc định chọn parent.

Đọc REST `id` của repository đã chọn như integer dương thực và giữ nó cùng host/
tên canonical suốt lifecycle đã duyệt. Dùng identity setup và
`Assert-SelectedRepositoryId` trong `github-setup.md`. Truyền
`--expected-repository-id` cho repository-settings, security-feature,
merge-settings, dependency-review, Scorecard, release, workflow-installation,
CodeQL default/advanced và branch-protection preflight; discovery result chưa
bind không phải quyền mutation. Từ chối ID đổi/mất hoặc bị coercion ở mọi
consumer result được hỗ trợ. Không âm thầm seed lại identity từ repository thay
thế cùng tên. Revalidation giảm race identity nhưng không tự tạo atomic
precondition cho REST write theo tên; ghi rõ race còn lại nếu có.

Kiểm tra community-health location được hỗ trợ theo thứ tự precedence của
GitHub: `.github/`, root, rồi `docs/`. Giữ active path đã chọn, không tạo bản
trùng ưu tiên cao hơn. Trước khi đề xuất override cục bộ cho repository không
phải fork, kiểm tra policy kế thừa có hiệu lực. Repository `.github` công khai
của owner có thể cung cấp mặc định; lookup không truy cập được là indeterminate,
không chứng minh không có policy. Thêm issue-template file cục bộ sẽ vô hiệu cả
thư mục issue-template kế thừa như một nhóm.
