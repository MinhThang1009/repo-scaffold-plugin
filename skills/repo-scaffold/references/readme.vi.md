**Ngôn ngữ:** [English](readme.md) | [Tiếng Việt](readme.vi.md)

# Cấu trúc README

Tạo README từ dữ kiện dự án thật, không bịa tính năng. Đặt ở root repo. GitHub
chọn README theo thứ tự `.github`, root, `docs/` (`.github` > root > `docs/`);
root là lựa chọn theo quy ước.

## Nội dung bắt buộc

Bao gồm các nội dung GitHub khuyến nghị trong “About READMEs”: dự án làm gì, vì
sao hữu ích, cách bắt đầu, nơi xin trợ giúp, ai duy trì và đóng góp.

## Header căn giữa

Dùng header căn giữa trong `../assets/README-header.md` (title + tagline + CI/
license badge tùy chọn). Thay toàn dòng `{{REPO_SCAFFOLD_CI_BADGE}}` và
`{{REPO_SCAFFOLD_LICENSE_BADGE}}`: chỉ xuất hiện khi remote GitHub.com đã xác
minh; chỉ xuất CI badge khi `.github/workflows/ci.yml` đã cài thật và không còn
fail-closed sentinel. Resolve link license badge đến path license đang có hoặc
sẽ tạo, không mặc định tên tệp `LICENSE`; URL-encode đích link tương đối. Xóa
từng marker nếu capability không có, gồm mọi scaffold chỉ chạy local hoặc trên
host không hỗ trợ.

HTML sanitizer của GitHub cho phép thuộc tính `align` trên block element (`div`,
`p`, `h1`–`h6`) và `img`, vì vậy `<div align="center">` là cách được chấp thuận
để căn giữa; CSS (`style="text-align:center"`) bị loại và không hoạt động. Giữ
dòng trống bên trong `<div>` để Markdown (heading, badge) được render.

## Section và subsection đánh số

Dùng H1 title (bên trong header căn giữa), sau đó section và subsection đánh số:

```markdown
## 1. Overview
## 2. Requirements
## 3. Installation
### 3.1 From source
### 3.2 From release
## 4. Usage
## 5. Configuration
## 6. Contributing
## 7. License
```

Đánh số là style chính thức được chọn theo sở thích; README GitHub thông thường
hay dùng heading không đánh số. GitHub tự tạo anchor cho heading theo cả hai
cách nên đánh số không làm hỏng điều hướng.

## Mục lục

README dài khi có từ tám H2 section đánh số trở lên. Với README dài, thêm mục lục
thủ công gần đầu (ngay dưới header): danh sách bullet link đến mọi section và
subsection đánh số. Khi cần, dịch heading mục lục theo phần còn lại. GitHub tạo
anchor bằng cách viết thường heading, thay dấu cách thành gạch nối và bỏ dấu câu;
ví dụ `## 3. Installation` thành `#3-installation`:

```markdown
## Table of Contents

- [1. Overview](#1-overview)
- [2. Requirements](#2-requirements)
- [3. Installation](#3-installation)
- [4. Configuration](#4-configuration)
  - [4.1 General settings](#41-general-settings)
  - [4.2 Advanced options](#42-advanced-options)
```

Khi section có subsection đánh số (4.1, 4.2…), lồng chúng thành bullet thụt vào
dưới section cha để mục lục phản ánh outline. Anchor subsection theo cùng quy
tắc: bỏ dấu chấm, nên `### 4.1 General settings` thành `#41-general-settings`
(không có dấu chấm giữa 4 và 1).

README ngắn thì bỏ mục lục thủ công và dùng outline menu tự sinh của GitHub trong
view đã render.

## Link và ảnh

Dùng relative link tới tệp khác trong repo (ví dụ `` `[Contributing](CONTRIBUTING.md)` ``,
`docs/CONTRIBUTING.md`), không dùng absolute `https://github.com/...`; GitHub
khuyến nghị relative link để hoạt động trong clone, fork và host khác. Ảnh nhúng
cũng vậy, tham chiếu tệp repo bằng path tương đối (`docs/screenshot.png`). Tài
nguyên ngoài (website dự án, CI badge endpoint) vẫn dùng URL tuyệt đối.
