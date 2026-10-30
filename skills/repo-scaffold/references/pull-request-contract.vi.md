**Ngôn ngữ:** [English](pull-request-contract.md) | [Tiếng Việt](pull-request-contract.vi.md)

# Hợp đồng pull request

Chỉ đọc tài liệu này khi thao tác tạo hoặc cập nhật PR đã được cho phép.

Trước khi tạo hoặc sửa PR, chạy `python scripts/pr_template_preflight.py
--title "<title>"` trong repository mục tiêu. Lệnh xác minh catalog template đã
check-in và xác định template mà title yêu cầu. Với review tập trung về security,
deployment hoặc dependency-update mà title không có mapping bắt buộc, truyền
`--template security`, `--template deployment` hoặc
`--template dependency-update`. Với PR title Conventional Commit bắt đầu bằng
`feat`, `fix` hoặc `docs`, lần lượt chọn `feature`, `bugfix` hoặc `documentation`.
Gate `pr-template` từ chối marker hoặc override không khớp. Với title type khác,
dùng template mặc định trừ khi thay đổi thực sự cần review tập trung.

Giữ đúng một marker `<!-- repo-scaffold:pr-template=<id> -->`, mọi heading bắt
buộc và mọi item required-checklist. Optional checklist chỉ là hướng dẫn; thêm
những mục phù hợp mà thôi. Khi kiểm tra heading/checklist bắt buộc, gate bỏ qua
fenced code, comment và raw HTML block; nội dung ẩn không thỏa contract cấu trúc.

Sau khi chuẩn bị body file UTF-8, chạy lại preflight với
`--body-file <path> --require-structure` để từ chối prose bị hard-wrap hoặc thiếu
heading/checklist bắt buộc trước GitHub mutation. Kiểm tra cấu trúc cho phép
draft item chưa tick; template gate remote kiểm tra riêng các mục đã tick khi PR
sẵn sàng review. Strict CLI bind body marker với title/`--template` đã resolve,
nên review tập trung không thể pass với body theo template mặc định.

Template discovery quét tối đa 10.000 entry trong mỗi candidate directory và
nhận tối đa 128 focused template có extension `.md`, `.markdown` hoặc `.txt`
(không phân biệt hoa thường). Template đã chọn và body file được cung cấp đều
giới hạn 1 MiB UTF-8 hợp lệ. Catalog checker tìm trong `.github/`, root repo,
`docs/` theo thứ tự cục bộ này. GitHub tài liệu hóa các vị trí đó nhưng không
nêu default nào thắng khi tồn tại nhiều nơi; preflight chọn deterministic ứng
viên đầu tiên theo thứ tự riêng. Nếu một vị trí có nhiều default template,
preflight từ chối catalog vì nhập nhằng.

Thay guidance bằng bằng chứng xác minh cụ thể. Draft PR có thể để required item
chưa tick; trước khi chuyển ready-for-review, chỉ tick sau khi công việc hoàn
tất. Ghi PR text UTF-8 vào file và dùng `gh pr create --body-file` hoặc
`gh pr edit --body-file`; không bypass template bằng `--fill` hoặc body tự do.

Review từng section được tạo so với toàn bộ PR diff. Nêu hành vi/kết quả bị ảnh
hưởng trong Purpose, lỗi đã xác nhận trong Root cause và mọi thay đổi cuối cùng
có ý nghĩa trong vài bullet Key changes. Giải thích test và giới hạn đã biết
trong Verification. Giữ acceptance criteria, ghi chú security/risk và kế hoạch
rollout/rollback nếu template đã chọn yêu cầu; automation không được thay các
quyết định đó bằng thống kê số file.

Workflow tùy chọn `pr-body-sync.yml` dùng commit field `Why:`, `Root cause:`,
`Changes:` và `Verification:`. Field có thể chứa paragraph hoặc bullet ở các
dòng sau, nhưng mỗi paragraph/bullet do tác giả viết phải nằm trên một dòng vật
lý. Không hard-wrap commit prose ở 72 hoặc 80 ký tự; ngăn paragraph và field
bằng dòng trống, giữ newline có chủ ý trong code block. Message cũ đã wrap vẫn
được hỗ trợ: renderer nối phần tiếp theo mà không sửa commit đã phát hành.
Dùng ngôn ngữ dự án đã chọn cho giá trị field.

Với PR dài, dùng commit đã được maintainer review, có
`PR-summary-base: <full-current-base-SHA>` và cả bốn field để ghi cumulative
summary đến commit đó. Workflow xác minh base SHA và field bắt buộc, nhưng không
thể xác minh một người đã review summary hay claim có khớp diff. Mọi thay đổi
sau đó vẫn thuộc phạm vi. Đối chiếu API commit/file count, giữ revert và từ chối
chủ đề vượt giới hạn thay vì âm thầm bỏ. Nếu thiếu bằng chứng về nguyên nhân,
phải nói rõ. Summary tích lũy phải bao quát hành vi chính và thay đổi safety
boundary, không chỉ PR-body tooling hoặc tài liệu.

Purpose, cause, key changes và verification lấy từ commit được gắn nhãn
author-reported tại full source SHA; nhắc trạng thái tab Checks được tạo riêng.
Changed-file inventory chứng minh số file trả về nhưng không xác minh narrative
claim với patch. Dùng GitHub Checks cho revision hiện tại. Sau khi render và sau
khi GitHub chấp nhận cập nhật, review toàn bộ body với diff.

Renderer CLI xác minh cấu trúc render đầy đủ với template check-in đã chọn trước
khi ghi output. Repository root mặc định là working directory hiện tại; dùng
`--repository-root` khi chạy nơi khác. Workflow chạy CLI từ trusted base checkout
nên body thiếu sẽ fail trước khi thay body file hoặc GitHub description.
Renderer nhận diện generated section bằng exact heading alias bên ngoài code,
comment và raw HTML. Section custom về risk, acceptance, rollback và
verification/monitoring kết hợp vẫn do tác giả sở hữu. Legacy head metadata chỉ
bị xóa khỏi body preamble; protocol example ở vị trí khác được giữ.

Native Dependabot body chỉ được giữ với account đã xác thực `dependabot[bot]`
và API user type `Bot`. Native Release Please body chỉ được giữ trên branch
`release-please--branches--` trong base repository, do bot hoặc repository owner
tạo. Quy tắc ownership khớp template gate; chỉ tên branch là chưa đủ. Renderer
vẫn xác minh PR đang mở, body hiện tại, head/base chính xác, commit/file inventory
đầy đủ và resource bound trước khi giữ automation body.

GitHub pull-request update endpoint không có conditional body-write parameter.
Nếu người dùng sửa body sau lần đọc cuối của workflow nhưng trước update, nội
dung có thể bị ghi đè; workflow rút ngắn khoảng này rồi xác minh kết quả.

Bind mọi receipt ban đầu, trước update và sau update với PR number được yêu cầu
cùng numeric base/head repository ID ban đầu, không chỉ tên và SHA khớp. Yêu
cầu base repository đang hoạt động và commit/file count có kiểu integer trước
khi so snapshot; Boolean hoặc float equality không phải bằng chứng count hợp lệ.
Từ chối JSON có member trùng thay vì để receipt nhập nhằng chọn giá trị cuối.
Các lần kiểm tra lại này không tạo conditional body-write hoặc xóa cửa sổ race
đã nêu.

Legacy body output chỉ nhận nghĩa vụ cleanup sau khi đã tạo temporary file. Lỗi
publish và cleanup phải báo lỗi có kiểm soát, không báo thành công; giữ lỗi
chính nếu cleanup cũng lỗi. Cleanup thất bại có thể để lại temp file thuộc run
cần review. Không khẳng định destination không đổi nếu publish có thể đã thành
công.

Renderer tự thêm dòng trạng thái tab Checks hiện tại; không nhắc lại live status
đó trong giá trị structured `Verification:`. Cumulative summary tối đa năm
bullet key changes và bốn verification note. Gộp evidence liên quan nhưng
không bỏ phạm vi quan trọng; renderer thêm lời nhắc tab Checks riêng với bốn
note đó.

Trong body file, mỗi paragraph prose và mỗi list item nằm trên một dòng vật lý;
ngăn paragraph bằng dòng trống để GitHub tự wrap theo chiều rộng view. GitHub
render line break trong issue/PR body thành line break, nên không hard-wrap prose
theo cột cố định; xem [hướng dẫn line-break](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#line-breaks).

Checker tuân theo GFM block parsing, giữ fenced/indented code, code span,
autolink, inline/block HTML, comment, heading, table, block quote và nested list,
đồng thời từ chối prose hard-wrap. Nhận diện table khi delimiter row khớp header,
kể cả table bắt đầu trong paragraph đang mở mà không có dòng trống ngăn trước.
Link reference definition hợp lệ là metadata không render, không phải prose.
Chúng có thể đứng cạnh nhau hoặc có label/title nhiều dòng, nhưng không được
ngắt paragraph đang có. Definition sai định dạng vẫn chịu prose check thông
thường. Dòng definition opaque không thể đáp ứng required heading/checklist;
destination được parse như dữ liệu, không gửi network request.

Reference scan dùng chung ngân sách 4.194.304 ký tự mỗi body; hết ngân sách thì
fail closed. Container indentation dùng tab stop bốn cột, nhưng tab trong
reference text vẫn là ký tự literal khi áp giới hạn label 999 ký tự. Inline
code/comment disambiguation giới hạn 4.194.304 ký tự scan trên mỗi body hoặc
template; vượt ngân sách thì validation fail closed.
