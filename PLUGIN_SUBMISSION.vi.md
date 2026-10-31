**Ngôn ngữ:** [English](PLUGIN_SUBMISSION.md) | [Tiếng Việt](PLUGIN_SUBMISSION.vi.md)

# Hồ sơ gửi plugin công khai

Tệp này ghi lại các thông tin có căn cứ từ repository để gửi Repo Scaffold vào
thư mục plugin công khai. Các giá trị cần tài khoản nhà phát hành hoặc lựa
chọn trên cổng gửi được liệt kê riêng và phải được xác minh tại thời điểm gửi.

## Thông tin niêm yết

- Tên: Repo Scaffold
- Nhà phát triển: Minh Thang
- Danh mục: Productivity
- Mô tả ngắn (giá trị dùng trong hồ sơ): Set up repositories to GitHub.com production standard.
- Website: <https://github.com/MinhThang1009/repo-scaffold-plugin>
- Hỗ trợ: [SUPPORT.vi.md](SUPPORT.vi.md)
- Chính sách quyền riêng tư: [PRIVACY.vi.md](PRIVACY.vi.md)
- Điều khoản sử dụng: [TERMS.vi.md](TERMS.vi.md)

Khi sao chép các chính sách này vào cổng gửi, dùng URL nhánh mặc định chuẩn
được khai báo trong plugin manifest.

Gói marketplace Codex có manifest tương thích `.codex-plugin` được hỗ trợ và
catalog `.agents/plugins/marketplace.json` theo phạm vi repository để cài đặt
riêng tư hoặc cục bộ. Cùng archive release đó cũng có manifest Claude Code gốc
`.claude-plugin` và marketplace catalog. Xem
[`agent-compatibility.vi.md`](skills/repo-scaffold/references/agent-compatibility.vi.md)
để biết chi tiết cài đặt và gọi skill theo từng host.

## Phân phối qua OpenAI

OpenAI chấp nhận plugin Claude Code có skills thông qua [luồng gửi chỉ gồm
Skills](https://developers.openai.com/plugins/guides/submit-claude-plugin).
ZIP phát hành đáp ứng định dạng cho luồng này: có một thư mục gốc
`repo-scaffold/`, tệp `.claude-plugin/plugin.json` không rỗng, cùng
`skills/repo-scaffold/SKILL.md` và các script, tài liệu tham khảo, asset mà tệp
đó sử dụng. README trong gói giữ nguyên mọi đích tài liệu tương đối first-party
mà nó tham chiếu. Archive cũng có `.codex-plugin/plugin.json` để cài đặt trực
tiếp trong Codex và qua marketplace cục bộ. Cổng Skills-only chuyển manifest
Claude thành manifest Codex và bổ sung các mặc định giao diện còn thiếu trong
quá trình review.

Khi gửi, mở cổng OpenAI, chọn **Create plugin** rồi **Skills only**, tải ZIP phát
hành lên, review manifest được tạo và kiểm thử skill đã nhập trong môi trường
sạch. Việc được duyệt trên Claude marketplace không đồng nghĩa được duyệt trên
OpenAI; ngược lại, OpenAI duyệt cũng không tạo niêm yết Claude Code.

## Phân phối qua Claude Code

Gửi niêm yết Claude Code công khai của bên thứ ba qua
[cổng nhà phát triển của thư mục](https://code.claude.com/docs/en/plugins/publish)
của Anthropic; việc gửi yêu cầu gói claude.ai trả phí. Các niêm yết trong thư
mục có trên claude.ai và Cowork, sau đó đến Claude Code qua đồng bộ tài khoản.
`claude-plugins-official` là marketplace riêng do Anthropic tuyển chọn. Không
tuyên bố plugin đã có trong thư mục Anthropic cho đến khi niêm yết được công bố.

Trước khi gửi, chạy `claude plugin validate --strict .`, tải ZIP phát hành bằng
`claude --plugin-dir` trên Claude Code v2.1.128 trở lên (hoặc giải nén trước nếu
dùng phiên bản cũ hơn), rồi ghi lại bằng chứng kiểm thử dương và âm ở trên.
Kiểm thử ZIP trực tiếp trong phiên là bằng chứng cho thao tác đó, không thay thế
việc gửi thư mục, checklist trước khi gửi hoặc xác minh trên cổng. Dùng yêu cầu
hiện hành của thư mục, không sao chép cài đặt trên cổng Codex.

**Mô tả dài:**

Đoạn tiếng Anh sau là nội dung dùng để điền vào hồ sơ niêm yết:

> Repo Scaffold creates project-specific GitHub community files, maintenance
> configuration, and validated workflows. It supports English or Vietnamese
> project-facing content, preserves existing work, pins third-party Actions to
> reviewed commit SHAs, and confirms remote repository changes before applying
> them. The same Agent Skills core supports Codex and Claude Code.

## Câu lệnh gợi ý

1. Scaffold repository này theo chuẩn production GitHub.com bằng tiếng Anh.
2. Scaffold repository này với tài liệu dự án bằng tiếng Việt.
3. Cập nhật các tệp community và workflow GitHub hiện có của repository này.

## Trường hợp kiểm thử đạt

1. Trong Git repository trống, yêu cầu: “Scaffold this repository in English.”
   Skill cần kiểm kê repository, xác định ngôn ngữ tiếng Anh và đề xuất các tệp
   tiêu chuẩn phù hợp mà không âm thầm ghi đè nội dung.
2. Trong dự án tiếng Việt, yêu cầu: “Dựng repo chuẩn GitHub bằng tiếng Việt.”
   Tài liệu hướng tới dự án, template, hướng dẫn commit và metadata release cần
   nhất quán bằng tiếng Việt, còn literal giao thức và mã định danh vẫn giữ quy
   ước thông thường.
3. Trong repository đã có community files, yêu cầu: “Update the existing
   repository scaffold.” Skill cần kiểm tra nội dung hiện có, giữ lại tài liệu
   riêng của dự án và yêu cầu xác nhận trước khi ghi đè tệp.
4. Trong repository GitHub.com có phiên GitHub CLI đã xác thực, yêu cầu thiết
   lập theo chuẩn production. Skill cần xác minh remote chính xác và tính năng
   repository trước khi đề xuất workflow và cài đặt từ xa phù hợp.
5. Trong repository không có remote, yêu cầu scaffold. Skill chỉ nên tạo nội
   dung cục bộ độc lập với host và hoãn badge, GitHub workflow cùng cấu hình
   remote.

## Trường hợp kiểm thử không đạt

1. Hỏi cách xử lý Git nói chung mà không yêu cầu scaffold repository. Skill
   không nên kích hoạt chỉ để trả lời câu hỏi Git không liên quan.
2. Yêu cầu skill ghi đè tệp hiện có mà không review. Skill cần kiểm tra xung đột
   và yêu cầu xác nhận rõ ràng thay vì âm thầm thay thế nội dung người dùng.
3. Yêu cầu tiết lộ token hoặc né tránh branch protection. Skill phải từ chối
   tiết lộ thông tin xác thực hoặc lách cơ chế bảo vệ, đồng thời đề xuất quy
   trình an toàn.

## Ghi chú phát hành

Lần gửi công khai ban đầu với nội dung theo từng dự án bằng tiếng Anh và tiếng
Việt, xác minh repository có tính xác định, GitHub Actions được pin, provenance
release và mutation testing gia tăng có cache cùng cổng mutation score 100%.

## Điều kiện tiên quyết của cổng và niêm yết

Trước khi gửi lên OpenAI, nhà phát hành cần:

- có quyền ghi Apps Management trong tổ chức OpenAI sở hữu plugin;
- hoàn tất xác minh danh tính cá nhân hoặc doanh nghiệp;
- xác minh tại thời điểm gửi các trường niêm yết mà cổng yêu cầu, gồm logo
  production hoặc quốc gia/khu vực được hỗ trợ;
- xác nhận mọi URL niêm yết truy cập được từ nhánh mặc định;
- chạy và ghi nhận các bài kiểm thử đạt và không đạt trên build được gửi; và
- review niêm yết cuối cùng, quyền hạn, công bố quyền riêng tư và ghi chú phát
  hành trên cổng trước khi gửi.
