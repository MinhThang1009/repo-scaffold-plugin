# Tài liệu cấu hình GitHub

**Ngôn ngữ:** [English](github-setup.md) | [Tiếng Việt](github-setup.vi.md)

Các lệnh `gh` chính xác cho bước 5 (cấu hình GitHub). Trước khi chạy, xác nhận
từng hành động hướng ra bên ngoài.

Tài liệu này chỉ hỗ trợ GitHub.com. Trước mọi lệnh, xác minh host remote đúng
`github.com`, ghi `OWNER/REPO` canonical và dùng `github.com/OWNER/REPO` cho lệnh
repository CLI cùng `--hostname github.com` cho API. Dừng cấu hình remote với
GitHub Enterprise Server hoặc GHE.com; workflow/lệnh đi kèm không phải cấu hình
enterprise host portable. Không dựa vào `GH_HOST`, thư mục hiện tại hoặc
repository mặc định của gh.

LƯU Ý (Windows/Git-Bash): path của `gh api` không được bắt đầu bằng dấu `/`, nếu
không shell có thể đổi thành filesystem path (`C:/Program Files/Git/...`). Dùng
`gh api --hostname github.com repos/OWNER/REPO/...`, tuyệt đối không dùng
`gh api --hostname github.com /repos/...`.

`inspection_complete` trong preflight phải là JSON Boolean. Từ chối string,
number, array, giá trị thiếu và type nhập nhằng khác trước khi dùng quyết định
để cho phép write hoặc copy asset; PowerShell truthiness không phải protocol
validation. Mỗi verdict phải là một JSON object, identity/decision field là
scalar text. Từ chối array root trước parse để host-specific enumeration không
biến array một phần tử thành object có vẻ hợp lệ. Xác minh collection và input
type của mutation plan trước cast, comparison hoặc normalization.

## Mục lục

- [Preflight danh tính repository](#preflight-danh-tính-repository)
- [Mô tả và chủ đề](#mô-tả-và-chủ-đề)
- [Tính năng giao tiếp repository](#tính-năng-giao-tiếp-repository)
- [Preflight cài workflow](#preflight-cài-workflow)
- [Policy community-health kế thừa](#policy-community-health-kế-thừa)
- [Branch protection (classic)](#branch-protection-classic)
- [Tương thích ruleset (chỉ kiểm tra)](#tương-thích-ruleset-chỉ-kiểm-tra)
- [Label](#label)
- [Dependabot](#dependabot)
- [Tính năng bảo mật](#tính-năng-bảo-mật)
- [Merge setting](#merge-setting)
- [Token release-please](#token-cho-release-please-release_please_token)
- [Xác minh](#xác-minh)

## Preflight danh tính repository

Caller control phải là Boolean thật, không phải string/number/collection/null
được diễn giải theo truthiness. Release attestation/plan/secret check,
merge-method removal/auto-merge request và CodeQL absence/admin requirement từ
chối library input sai trước khi inspection. CLI `store_true` default giữ
nguyên; CodeQL administration requirement tùy chọn bị thiếu vẫn theo documented
false default. Confirmation đúng type không chứng minh nguồn gốc từ người dùng,
approval gắn target hoặc entitlement platform hiệu lực.

Resolve target trước query GitHub đầu tiên. `gh repo view` không argument có thể
theo repository mặc định đã cấu hình trong gh; khi clone fork bằng
[`gh repo clone`](https://cli.github.com/manual/gh_repo_clone), GitHub CLI đặt
parent làm mặc định trừ khi dùng `--no-upstream`. Coi `gh repo set-default
--view` chỉ là output chẩn đoán.

```powershell
function ConvertTo-GitHubRepository {
  param([Parameter(Mandatory)][string]$RemoteUrl)

  $owner = $null
  $repo = $null
  $uri = $null
  $isAbsoluteUri = [Uri]::TryCreate($RemoteUrl, [UriKind]::Absolute, [ref]$uri)
  if ($isAbsoluteUri -and
      [System.StringComparer]::OrdinalIgnoreCase.Equals($uri.Host, "github.com")) {
    if ($uri.Scheme -notin @("https", "ssh")) {
      throw "A GitHub.com remote uses an unsupported URL scheme."
    }
    if (($uri.Scheme -eq "https" -and -not [string]::IsNullOrEmpty($uri.UserInfo)) -or
        ($uri.Scheme -eq "ssh" -and $uri.UserInfo -ne "git")) {
      throw "A GitHub.com remote contains unsupported user information; do not print the URL because it may contain credentials."
    }
    $segments = @($uri.AbsolutePath.Trim('/') -split '/')
    if ($segments.Count -ne 2) { throw "A GitHub.com remote does not identify one OWNER/REPO pair." }
    $owner, $repo = $segments
  } elseif ($isAbsoluteUri) {
    return $null
  } else {
    $scpMatch = [regex]::Match(
      $RemoteUrl,
      '^(?i:git@github\.com):([^/]+)/([^/]+)$'
    )
    if ($scpMatch.Success) {
      $owner = $scpMatch.Groups[1].Value
      $repo = $scpMatch.Groups[2].Value
    } elseif ($RemoteUrl -match '(?i)(?:^|@)github\.com[:/]') {
      throw "A GitHub.com remote URL is malformed or unsupported; do not guess its repository."
    } else {
      return $null
    }
  }

  if ($repo.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
    $repo = $repo.Substring(0, $repo.Length - 4)
  }
  if ([string]::IsNullOrWhiteSpace($owner) -or [string]::IsNullOrWhiteSpace($repo) -or
      $owner -in @(".", "..") -or $repo -in @(".", "..") -or
      $owner -notmatch '^[A-Za-z0-9_.-]+$' -or
      $repo -notmatch '^[A-Za-z0-9_.-]+$') {
    throw "A GitHub.com remote has an invalid OWNER/REPO value."
  }
  return "$owner/$repo"
}

$remoteNames = @(git -C $REPO_ROOT remote)
if ($LASTEXITCODE -ne 0) { throw "Failed to list git remotes." }

$remoteRepositoryMappings = @()
foreach ($remoteName in $remoteNames) {
  $remoteUrlOutput = git -C $REPO_ROOT remote get-url --all -- $remoteName 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to read the fetch URLs for remote '$remoteName'."
  }
  foreach ($remoteUrl in @($remoteUrlOutput)) {
    $repository = ConvertTo-GitHubRepository ([string]$remoteUrl)
    if ($null -ne $repository) {
      $remoteRepositoryMappings += [pscustomobject]@{
        RemoteName = $remoteName
        Repository = $repository
      }
    }
  }
}

$repositoryCandidates = @(
  $remoteRepositoryMappings.Repository | Sort-Object -Unique
)
# Display this canonical mapping, never the raw URLs. A raw HTTPS remote can contain credentials.
$remoteRepositoryMappings
$configuredGhDefault = gh repo set-default --view 2>&1
# Diagnostic only; never add $configuredGhDefault to $repositoryCandidates or use it to select one.
```

Khi không có ứng viên GitHub.com, dừng remote discovery và tiếp tục local-only.
Nếu có một ứng viên, dùng nó. Nếu có nhiều, hiển thị remote mapping và hỏi người
dùng chọn; không ưu tiên `origin`, `upstream` hoặc mặc định của gh. Sau khi
chọn, chỉ query repository tường minh và đối chiếu response:

```powershell
$selectedRepository = "OWNER/REPO" # selected from the verified remote candidates
$repoViewOutput = gh repo view "github.com/$selectedRepository" `
  --json nameWithOwner,url,owner,defaultBranchRef,visibility,isFork,isArchived 2>&1
if ($LASTEXITCODE -ne 0) { throw "Failed to read the selected GitHub repository." }
$repoViewText = $repoViewOutput | Out-String
if (-not $repoViewText.TrimStart().StartsWith("{")) {
  throw "Selected repository view must be one JSON object; do not bind the target."
}
$repoView = $repoViewText | ConvertFrom-Json -ErrorAction Stop
if ($repoView -isnot [System.Management.Automation.PSCustomObject] -or
    $repoView.nameWithOwner -isnot [string] -or $repoView.url -isnot [string]) {
  throw "Selected repository view has an invalid identity schema; do not bind the target."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  $repoView.nameWithOwner,
  $selectedRepository
)) {
  throw "GitHub returned a different repository than the selected remote candidate."
}
$repoHost = ([Uri]$repoView.url).Host
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($repoHost, "github.com")) {
  throw "The selected repository is not hosted on GitHub.com."
}

$selectedIdentityOutput = gh api --hostname github.com "repos/$selectedRepository" 2>&1
if ($LASTEXITCODE -ne 0) { throw "Could not bind the selected repository identity; do not mutate." }
$selectedIdentityText = $selectedIdentityOutput | Out-String
if (-not $selectedIdentityText.TrimStart().StartsWith("{")) {
  throw "Selected repository identity must be one JSON object; do not bind the target."
}
$selectedIdentity = $selectedIdentityText | ConvertFrom-Json -ErrorAction Stop
if ($selectedIdentity -isnot [System.Management.Automation.PSCustomObject] -or
    $selectedIdentity.full_name -isnot [string] -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals($selectedIdentity.full_name, $selectedRepository) -or
    ($selectedIdentity.id -isnot [int] -and $selectedIdentity.id -isnot [long]) -or
    $selectedIdentity.id -le 0) {
  throw "Selected repository identity is missing, malformed, or mismatched; do not mutate."
}
$SELECTED_REPOSITORY_ID = $selectedIdentity.id

function Assert-SelectedRepositoryId {
  param([Parameter(Mandatory)][AllowNull()][object]$RepositoryId)
  if (($SELECTED_REPOSITORY_ID -isnot [int] -and $SELECTED_REPOSITORY_ID -isnot [long]) -or
      $SELECTED_REPOSITORY_ID -le 0 -or
      ($RepositoryId -isnot [int] -and $RepositoryId -isnot [long]) -or
      $RepositoryId -le 0 -or $RepositoryId -ne $SELECTED_REPOSITORY_ID) {
    throw "Numeric repository identity is unverified or changed since discovery; stop remote mutation and review the target."
  }
}
```

Giữ `SELECTED_REPOSITORY_ID` cho target đã duyệt; không seed lại từ response bất
ngờ về sau. Repository-settings, security-feature, merge-settings,
dependency-review, Scorecard, release, workflow-installation và CodeQL
default/advanced preflight nhận `--expected-repository-id`, trả `repository_id`
đúng type; truyền ID discovery trong mọi lần inspection lặp và xác minh ID trả
về trước khi tin mutation verdict. Chế độ inspection tùy chọn chưa bind trả
`bind-repository-identity-before-mutation`; nó chỉ discovery ID hiện tại, không
được cho phép mutation hoặc thay ID lưu từ discovery đã duyệt. Các feature
preflight khác vẫn phải được review để có identity binding tương đương trước
khi coi lifecycle hoàn tất. Không giả định REST call theo tên có numeric-ID
precondition atomic; check này không tuyên bố xóa mọi race giữa check và write.

Truyền `github.com/OWNER/REPO` cho mọi lệnh `gh` theo repository về sau. Sau
preflight này, không fallback sang query không argument.

## Mô tả và chủ đề

Ngay trước khi đổi metadata cơ bản hoặc communication setting, chạy bundled
`scripts/repository_settings_preflight.py`. Script chỉ đọc và fail-closed: bind
GitHub response với `OWNER/REPO` tường minh, từ chối repository archived/disabled,
yêu cầu quyền quản trị hiện tại và bind description/topic/Issues/Discussions đã
duyệt với mutation plan cuối. Không chạy `gh repo edit` nếu thiếu preflight, kết
quả inconclusive hoặc request được duyệt khác request này.

Preflight thực thi tên topic GitHub tối đa 50 ký tự và tối đa 20 topic, gồm cả
topic mới cộng với set hiện có được giữ. Nó không cho phép xóa topic cũ để tạo
chỗ. GitHub CLI cập nhật repository field và topic bằng các API request đồng
thời riêng; một lệnh edit gộp có thể áp dụng một phần. Đọc lại state kể cả khi
edit exit lỗi và báo kết quả partial/unverified.

```powershell
# Keep user/repository text as data. Do not generate a command string and invoke it.
$description = Read-Host "One-line repository description"
if ([string]::IsNullOrWhiteSpace($description)) {
  throw "Repository description must be non-empty."
}
$topics = @("topic1", "topic2") # confirmed GitHub topic slugs
$repositorySettingsPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/repository_settings_preflight.py"
if (-not (Test-Path -LiteralPath $repositorySettingsPreflight -PathType Leaf)) {
  throw "The bundled repository-settings preflight is missing; do not edit repository metadata."
}
$metadataPreflightArguments = @(
  "--repository", "OWNER/REPO", "--hostname", "github.com",
  "--expected-repository-id", $SELECTED_REPOSITORY_ID,
  "--set-description", "--description", $description
)
if ($topics.Count -gt 0) {
  $metadataPreflightArguments += "--set-topics"
  foreach ($topic in $topics) { $metadataPreflightArguments += @("--topic", $topic) }
}
$metadataPreflightOutput = python $repositorySettingsPreflight @metadataPreflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Repository metadata inspection is inconclusive; do not edit metadata. $($metadataPreflightOutput | Out-String)"
}
try {
  $metadataPreflightText = $metadataPreflightOutput | Out-String
  if (-not $metadataPreflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
  $metadataPreflight = $metadataPreflightText | ConvertFrom-Json -ErrorAction Stop
} catch {
  throw "Repository-settings preflight returned invalid JSON; do not edit metadata."
}
Assert-SelectedRepositoryId -RepositoryId $metadataPreflight.repository_id
if ($metadataPreflight -isnot [pscustomobject] -or
    $metadataPreflight.repository -isnot [string] -or
    $metadataPreflight.decision -isnot [string] -or
    $metadataPreflight.inspection_complete -isnot [bool] -or
    -not $metadataPreflight.inspection_complete -or
    $metadataPreflight.decision -ne "may-configure-repository-settings" -or
    @($metadataPreflight.requested_mutations) -notcontains "description") {
  throw "Repository-settings preflight did not approve the requested metadata mutations."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  [string]$metadataPreflight.repository, "OWNER/REPO"
)) {
  throw "Repository-settings preflight returned a different repository; do not mutate."
}
$approvedMetadata = $metadataPreflight.requested_settings
if ($approvedMetadata -isnot [pscustomobject] -or
    $approvedMetadata.description -isnot [string] -or
    $approvedMetadata.topics -isnot [Array] -or
    @($approvedMetadata.topics | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
    $metadataPreflight.requested_mutations -isnot [Array] -or
    @($metadataPreflight.requested_mutations | Where-Object { $_ -isnot [string] }).Count -gt 0) {
  throw "Repository-settings preflight did not return the approved metadata input."
}
$expectedMetadataMutations = @("description")
if ($topics.Count -gt 0) { $expectedMetadataMutations += "topics" }
if ($metadataPreflight.requested_mutations.Count -ne $expectedMetadataMutations.Count -or
    $null -ne (Compare-Object -ReferenceObject $expectedMetadataMutations -DifferenceObject $metadataPreflight.requested_mutations -CaseSensitive)) {
  throw "Metadata preflight does not approve exactly the requested mutations; do not edit metadata."
}
$approvedTopics = @($approvedMetadata.topics)
if ($topics.Count -gt 0 -and
    ($metadataPreflight.current_topics -isnot [Array] -or
     @($metadataPreflight.current_topics | Where-Object { $_ -isnot [string] }).Count -gt 0)) {
  throw "Preflight did not provide the preserved topic set; do not edit metadata."
}
$previousTopics = @()
if ($topics.Count -gt 0) { $previousTopics = @($metadataPreflight.current_topics) }
if ($approvedMetadata.description -cne $description -or
    $approvedTopics.Count -ne $topics.Count -or
    $null -ne (Compare-Object -ReferenceObject @($topics) -DifferenceObject $approvedTopics -CaseSensitive)) {
  throw "Repository-settings preflight input does not match the metadata mutation."
}
$topicArgs = @()
foreach ($topic in $topics) { $topicArgs += @('--add-topic', $topic.ToLowerInvariant()) }
$editOutput = & gh repo edit github.com/OWNER/REPO --description $description @topicArgs 2>&1
$editExitCode = $LASTEXITCODE

$metadataOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Metadata change may be partially applied and could not be verified. Edit exit: $editExitCode. $($metadataOutput | Out-String)"
}
$metadata = ($metadataOutput | Out-String) | ConvertFrom-Json
Assert-SelectedRepositoryId -RepositoryId $metadata.id
if ($metadata.full_name -isnot [string] -or
    -not [StringComparer]::OrdinalIgnoreCase.Equals($metadata.full_name, "OWNER/REPO") -or
    $metadata.description -isnot [string] -or $metadata.topics -isnot [Array] -or
    @($metadata.topics | Where-Object { $_ -isnot [string] }).Count -gt 0) {
  throw "Metadata change may be partially applied; readback schema or repository binding is invalid."
}
$actualTopics = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($topic in @($metadata.topics)) { [void]$actualTopics.Add([string]$topic) }
$missingTopics = @($topics | Where-Object { -not $actualTopics.Contains($_) })
$missingPreviousTopics = @($previousTopics | Where-Object { -not $actualTopics.Contains($_) })
if ($metadata.description -cne $description -or $missingTopics.Count -gt 0 -or
    $missingPreviousTopics.Count -gt 0) {
  throw "Metadata change may be partially applied or concurrently changed. Description matches: $($metadata.description -ceq $description). Missing requested topics: $($missingTopics -join ', '). Missing preserved topics: $($missingPreviousTopics -join ', ')."
}
if ($editExitCode -ne 0) {
  Write-Warning "The edit failed, but readback verified the requested and preserved metadata state. $($editOutput | Out-String)"
}
```

Mô tả không rỗng là bắt buộc để đạt community profile 100%. Topic giúp người
dùng tìm repository.

## Tính năng giao tiếp repository

Kiểm tra các setting này trước khi tạo issue template hoặc liên kết giao tiếp:

```bash
gh repo view github.com/OWNER/REPO --json isArchived,hasIssuesEnabled,hasDiscussionsEnabled
```

Repository archived là read-only; không cấu hình remote cho chúng. Nếu repository
đang hoạt động nhưng Issues/Discussions bị tắt, hãy hỏi trước khi bật từng tính
năng. Chạy riêng các mutation đã duyệt, rồi query lại state trước khi render tệp
phụ thuộc:

```powershell
# Set these only from explicit user confirmation.
$enableIssuesRequested = $false
$enableDiscussionsRequested = $false
if ($enableIssuesRequested -isnot [bool] -or $enableDiscussionsRequested -isnot [bool]) {
  throw "Communication choices must be explicit Boolean values; do not infer approval."
}

if ($enableIssuesRequested -or $enableDiscussionsRequested) {
  $repositorySettingsPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/repository_settings_preflight.py"
  if (-not (Test-Path -LiteralPath $repositorySettingsPreflight -PathType Leaf)) {
    throw "The bundled repository-settings preflight is missing; do not enable communication features."
  }
  $communicationPreflightArguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID)
  if ($enableIssuesRequested) { $communicationPreflightArguments += "--enable-issues" }
  if ($enableDiscussionsRequested) { $communicationPreflightArguments += "--enable-discussions" }
  $communicationPreflightOutput = python $repositorySettingsPreflight @communicationPreflightArguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Communication-feature inspection is inconclusive; do not mutate. $($communicationPreflightOutput | Out-String)"
  }
  try {
    $communicationPreflightText = $communicationPreflightOutput | Out-String
    if (-not $communicationPreflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
    $communicationPreflight = $communicationPreflightText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Repository-settings preflight returned invalid JSON; do not mutate."
  }
  Assert-SelectedRepositoryId -RepositoryId $communicationPreflight.repository_id
  if ($communicationPreflight -isnot [pscustomobject] -or
      $communicationPreflight.repository -isnot [string] -or
      $communicationPreflight.decision -isnot [string] -or
      $communicationPreflight.inspection_complete -isnot [bool] -or
      -not $communicationPreflight.inspection_complete -or
      $communicationPreflight.decision -ne "may-configure-repository-settings") {
    throw "Repository-settings preflight did not approve the requested communication mutations."
  }
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$communicationPreflight.repository, "OWNER/REPO"
  )) {
    throw "Repository-settings preflight returned a different repository; do not mutate."
  }
  $approvedCommunication = $communicationPreflight.requested_settings
  if ($approvedCommunication -isnot [pscustomobject] -or
      $approvedCommunication.issues -isnot [bool] -or
      $approvedCommunication.discussions -isnot [bool] -or
      $approvedCommunication.issues -ne $enableIssuesRequested -or
      $approvedCommunication.discussions -ne $enableDiscussionsRequested) {
    throw "Repository-settings preflight input does not match the communication mutation."
  }
  $expectedCommunicationMutations = @()
  if ($enableIssuesRequested) { $expectedCommunicationMutations += "issues" }
  if ($enableDiscussionsRequested) { $expectedCommunicationMutations += "discussions" }
  if ($communicationPreflight.requested_mutations -isnot [Array] -or
      @($communicationPreflight.requested_mutations | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
      $communicationPreflight.requested_mutations.Count -ne $expectedCommunicationMutations.Count -or
      $null -ne (Compare-Object -ReferenceObject $expectedCommunicationMutations -DifferenceObject $communicationPreflight.requested_mutations -CaseSensitive)) {
    throw "Communication preflight does not approve exactly the requested mutations."
  }
}

function Test-FreshCommunicationPreflight {
  param([Parameter(Mandatory)][ValidateSet("issues", "discussions")][string]$Feature)

  $arguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID)
  if ($Feature -eq "issues") { $arguments += "--enable-issues" }
  if ($Feature -eq "discussions") { $arguments += "--enable-discussions" }
  $output = python $repositorySettingsPreflight @arguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    Write-Warning "Fresh $Feature preflight is inconclusive; skip this mutation. $($output | Out-String)"
    return $false
  }
  try {
    $preflightText = $output | Out-String
    if (-not $preflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
    $result = $preflightText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    Write-Warning "Fresh $Feature preflight returned invalid JSON; skip this mutation."
    return $false
  }
  if ($result -isnot [pscustomobject] -or
      $result.repository -isnot [string] -or $result.decision -isnot [string] -or
      $result.requested_mutations -isnot [Array] -or
      @($result.requested_mutations | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
      $result.requested_settings -isnot [pscustomobject] -or
      $result.requested_settings.issues -isnot [bool] -or
      $result.requested_settings.discussions -isnot [bool]) {
    Write-Warning "Fresh communication preflight has an invalid schema; skip this mutation."
    return $false
  }
  $mutations = @($result.requested_mutations)
  $expectedIssues = $Feature -eq "issues"
  $expectedDiscussions = $Feature -eq "discussions"
  Assert-SelectedRepositoryId -RepositoryId $result.repository_id
  if ($result.inspection_complete -isnot [bool] -or
      -not $result.inspection_complete -or
      $result.decision -ne "may-configure-repository-settings" -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$result.repository, "OWNER/REPO"
      ) -or $mutations.Count -ne 1 -or $mutations[0] -cne $Feature -or
      $null -eq $result.requested_settings -or
      $result.requested_settings.issues -ne $expectedIssues -or
      $result.requested_settings.discussions -ne $expectedDiscussions) {
    Write-Warning "Fresh $Feature preflight did not approve exactly that setting for OWNER/REPO; skip this mutation."
    return $false
  }
  return $true
}

if ($enableIssuesRequested) {
  if (Test-FreshCommunicationPreflight -Feature "issues") {
    $issuesOutput = & gh repo edit github.com/OWNER/REPO --enable-issues 2>&1
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "Issues could not be enabled; issue-dependent output will be omitted. $($issuesOutput | Out-String)"
    }
  }
}
if ($enableDiscussionsRequested) {
  if (Test-FreshCommunicationPreflight -Feature "discussions") {
    $discussionsOutput = & gh repo edit github.com/OWNER/REPO --enable-discussions 2>&1
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "Discussions could not be enabled; discussion-dependent output will be omitted. $($discussionsOutput | Out-String)"
    }
  }
}

$featureStateOutput = gh api --hostname github.com repos/OWNER/REPO
if ($LASTEXITCODE -ne 0) {
  throw "Could not verify repository communication features; omit dependent output."
}
$featureState = $featureStateOutput | ConvertFrom-Json
Assert-SelectedRepositoryId -RepositoryId $featureState.id
if ($featureState.full_name -isnot [string] -or
    -not [StringComparer]::OrdinalIgnoreCase.Equals($featureState.full_name, "OWNER/REPO") -or
    $featureState.archived -isnot [bool] -or $featureState.disabled -isnot [bool] -or
    $featureState.has_issues -isnot [bool] -or $featureState.has_discussions -isnot [bool]) {
  throw "Communication-feature readback is malformed or unbound; omit dependent output."
}
if ($featureState.archived -or $featureState.disabled) { throw "Repository is inactive; skip remote configuration." }
$hasIssuesEnabled = $featureState.has_issues
$hasDiscussionsEnabled = $featureState.has_discussions
```

Khi render template/link, chỉ dùng `$hasIssuesEnabled` và
`$hasDiscussionsEnabled` từ query cuối đó. Nếu feature vẫn tắt, bỏ output phụ
thuộc thay vì phát hành navigation chết. Với repository chỉ cục bộ, dùng liên hệ
ngoài GitHub đã xác nhận cho đến khi có remote; trạng thái dự kiến trong tương
lai không phải capability đã bật.

## Preflight cài workflow

Trước khi copy GitHub Actions asset, chạy bundled preflight chỉ đọc. Nó
bind response với đúng repository, từ chối repository archived/disabled, giới
hạn số workflow input/tổng byte và kiểm tra GitHub Actions đã bật. Asset có
`uses:` cần `--require-external-actions`. Policy `local_only` cấm asset đó, gồm
Docker container action. Với policy `selected`, truyền từng candidate asset bằng
`--workflow`; preflight theo `selected_actions_url` GitHub quảng bá (kể cả
override organization/enterprise) và kiểm tra từng exact action pin. Fail closed
trừ khi mỗi reference khớp allowlist pattern tường minh hoặc allowance
`actions/*`/`github/*`; negative pattern vẫn chặn. Pattern match chỉ được chấp
nhận ở repository công khai vì preflight không suy ra eligibility Enterprise
Cloud. Marketplace verified creator một mình không chứng minh action cụ thể
được phép.

Asset qua `--workflow` cho phép preflight tự suy ra yêu cầu external-action và
issue-workflow đi kèm. `--require-issues` là assertion tường minh cho workflow
ghi issue ngoài tên asset đã đóng gói. Nếu workflow gọi local reusable workflow,
truyền workflow được gọi như một `--workflow` input khác trong cùng invocation;
local call chưa resolve bị từ chối trước action-policy approval. Input được gọi
phải cùng workflow directory với caller; basename trùng từ thư mục khác là
ambiguous. Workflow được gọi phải khai báo `workflow_call`; từ chối vòng lặp.
Local call graph không vượt 10 workflow level và 50 nested workflow duy nhất
mỗi top-level caller.

Workflow khai báo `pull_request_target` cần review riêng event-policy ở
repository công khai. Policy mặc định GitHub dự kiến chặn event từ 2-12-2026
trừ khi workflow-execution policy áp dụng cho phép rõ. Kiểm tra inherited policy
bằng read-only [Actions policies API](https://docs.github.com/en/rest/actions/policies).
Workflow-installation preflight thực hiện check và yêu cầu rule
`restrict_action_events` đang active, cho phép `pull_request_target` ở mọi
workflow path được cung cấp. Thiếu bằng chứng thì hoãn cài; selected-actions
result không chứng minh event-policy và plugin không sửa remote policy.

Với project có lệnh test/lint chạy được, trước khi scaffold hoàn tất phải cài CI
workflow đã cấu hình hoặc ghi quyết định defer tường minh. Với mọi asset áp dụng
đã duyệt, truyền đúng path qua `--workflow`, copy companion bắt buộc và xác minh
tệp đã cài. Asset tùy chọn vẫn theo feature; ghi omission là not-applicable hoặc
deferred.

Workflow `pull_request` có write permission cần
`--confirm-pull-request-write-tokens`. Coi đây là operator assertion tường minh
về event/actor/target dự kiến, không phải bằng chứng chỉ từ YAML. Xác minh
effective token scope và policy repository/organization/enterprise áp dụng
trước khi xác nhận. Setting **Send write tokens to workflows from pull requests**
chỉ áp dụng fork PR của private repository; không phải điều kiện cho mọi PR
cùng repository và không được bật chỉ để cài asset. Token của external fork
công khai vẫn bị hạn chế. Dependabot automation cùng repository có thể yêu cầu
scope tường minh theo [hướng dẫn GitHub](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/automate-dependabot-with-actions).
Asset đi kèm không chạy PR code, chỉ xử lý Dependabot PR cùng repository. Review
đúng actor/event path và giữ scope hẹp; không thay bằng `pull_request_target` hoặc
thay đổi token policy quá rộng.

Không có bằng chứng permission hiệu lực thì giữ assertion false, hoãn cài.
Boolean validation không chứng minh approval provenance hoặc permission hiệu
lực; preflight không thay token policy.

```powershell
$workflowPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/workflow_installation_preflight.py"
if (-not (Test-Path -LiteralPath $workflowPreflight -PathType Leaf)) {
  throw "The bundled workflow-installation preflight is missing; do not copy workflow assets."
}
$workflowPreflightArguments = @(
  "--repository", "OWNER/REPO",
  "--expected-repository-id", $SELECTED_REPOSITORY_ID,
  "--hostname", "github.com",
  "--require-external-actions",
  "--workflow", "assets/workflows/ci.yml"
)
# For an issue-writing workflow that does not contain either marker, set this true.
# Every --workflow input containing issues: write or permissions: write-all is
# detected and cannot bypass the Issues check when this stays false.
$requiresIssueOperations = $false
if ($requiresIssueOperations -isnot [bool]) {
  throw "Issue-operation assertion must be a Boolean."
}
if ($requiresIssueOperations) { $workflowPreflightArguments += "--require-issues" }
$pullRequestWriteTokensConfirmed = $false
# Set only after verifying effective token scopes for the intended actor/event and target policy.
if ($pullRequestWriteTokensConfirmed -isnot [bool]) {
  throw "Pull-request write-token assertion must be a Boolean."
}
if ($pullRequestWriteTokensConfirmed) {
  $workflowPreflightArguments += "--confirm-pull-request-write-tokens"
}
$workflowPreflightOutput = python $workflowPreflight @workflowPreflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Workflow-installation inspection is inconclusive; do not copy the asset. $($workflowPreflightOutput | Out-String)"
}
$workflowPreflightText = $workflowPreflightOutput | Out-String
if (-not $workflowPreflightText.TrimStart().StartsWith("{")) { throw "Workflow preflight must be an object." }
$workflowPreflightResult = $workflowPreflightText | ConvertFrom-Json -ErrorAction Stop
Assert-SelectedRepositoryId -RepositoryId $workflowPreflightResult.repository_id
if ($workflowPreflightResult -isnot [pscustomobject] -or
    $workflowPreflightResult.repository -isnot [string] -or
    $workflowPreflightResult.decision -isnot [string] -or
    $workflowPreflightResult.inspection_complete -isnot [bool] -or
    -not $workflowPreflightResult.inspection_complete -or
    $workflowPreflightResult.decision -ne "may-install-workflow-assets") {
  throw "Workflow capability is not confirmed. Resolve the returned decision and rerun before copying the asset."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  [string]$workflowPreflightResult.repository, "OWNER/REPO"
)) {
  throw "Workflow-installation preflight returned a different repository; do not copy assets."
}
```

**Xác minh workflow đã bind.**

Kết quả workflow-installation phải tiếp tục gắn với ID discovery ban đầu. Chỉ
tên repository khớp là chưa đủ; discovery chưa bind không cho phép copy asset.
Inspection nhiều request đọc lại trạng thái repository điều khiển, Actions
permission, selected-actions policy và inherited event-policy snapshot trước khi
trả verdict. Numeric repository-scoped selected-actions URL phải khớp target ID;
organization/enterprise override vẫn dùng URL quảng bá đã xác minh. Drift hoặc
revalidation không khả dụng là inconclusive, không được dùng verdict cache.
Chạy lại đúng inspection ngay trước từng asset copy riêng; bước này không đảm
bảo API/file-copy atomic sau lần đọc cuối.

Khi cài code-scanning-gate.yml, truyền cả gate và freshness.yml bằng
--workflow trong cùng preflight, rồi truyền
assets/code-scanning-allowlist.json bằng --code-scanning-allowlist. Preflight
từ chối gate thiếu một trong hai companion; chỉ copy cả ba input đã xác minh khi
kết quả may-install-workflow-assets. Allowlist phải dùng schema v3 với selector
field chính xác, alert number dương duy nhất, POSIX path canonical không
traversal/ký tự điều khiển, ngày ISO review không ở tương lai và thời hạn
1–366 ngày. Field cấp cao nhất chỉ gồm schema-version và allowlist; entry sai
bị từ chối trước khi duyệt.

freshness.yml chỉ dùng trigger schedule và workflow_dispatch; cron có năm
field POSIX cùng IANA timezone hợp lệ tùy chọn; manual input rỗng hoặc mapping
hợp lệ tối đa 25 input. Chỉ yêu cầu contents: read và issues: write, chạy
checker và reconcile marker issue qua lệnh gh issue create hoặc gh issue edit
--repo ... --body-file được bind vào repository. Mọi create/edit dùng
--body-file, create cần --title không rỗng, close cũng cần --repo. Dùng
concurrency group ổn định theo repository với expression github.repository,
không hủy run. Manual issue-writing job checkout đúng default branch của event
với persist-credentials: false; trigger, comment, shell command nhập nhằng hoặc
reminder thiếu không thỏa companion contract.

Body file phải là Markdown output của audit trong cùng job. Issue lookup dùng
GitHub Search API GET có giới hạn, chỉ issue đang mở, marker freshness,
is:issue, in:body, per_page=2; yêu cầu incomplete_results Boolean false,
total_count integer không âm và đúng min(total_count, 2) issue number integer
dương, duy nhất. Projection đã kiểm tra thành một dòng shell-safe; zero match là
literal none, không phải stdout rỗng. Giữ guard lỗi output rỗng ngay sau lookup,
truyền kết quả đã xác minh vào close/edit, không seed issue state trước lookup và
không dùng sentinel làm Issue argument. Reconciliation bắt đầu bằng
set -euo pipefail, audit xong trước lookup rồi mới mutation; checker status
điều khiển clean/stale. Giới hạn đúng path output,
$RUNNER_TEMP/freshness.json và $RUNNER_TEMP/freshness.md, token binding,
shell, action pin, permission, concurrency, env, command set và registry/resource
caps theo hợp đồng chi tiết tại
[workflow-contracts.vi.md](workflow-contracts.vi.md). Không thêm override hoặc
lệnh che việc kiểm tra.

Checker community-health chỉ chấp nhận status 0, 1, 2; status khác phải fail
trước mutation. Freshness registry giữ đủ workflow/release/allowlist/requirement
input; schema v1 chỉ nhận field đã biết. Checker giới hạn workflow 5 MiB mỗi
file, inventory 500 file/64 MiB, 500 upstream action repo, 500 tracked path,
512 direct pin mỗi requirements file, 4096 pin tổng và 1 MiB cho requirements/
JSON policy input. Nếu có --tracker-registry, chỉ nhận
.github/freshness-trackers.json. Không xóa nhóm tracker để vô hiệu hóa check.

## Policy community-health kế thừa

Trước khi đề xuất community file cục bộ, kiểm tra default kế thừa từ repository
`.github` **công khai** của account. GitHub.com không áp dụng default account từ
repository `.github` internal/private. File cục bộ override default; chỉ cần có
một file `.github/ISSUE_TEMPLATE` cục bộ thì vô hiệu cả issue-template directory
kế thừa như một nhóm.

```powershell
$targetOutput = gh repo view github.com/OWNER/REPO --json name,owner,isFork
if ($LASTEXITCODE -ne 0) { throw "Failed to read the target repository." }
$target = $targetOutput | ConvertFrom-Json
$owner = $target.owner.login
$repo = $target.name

if (-not (Get-Variable REPO_ROOT -ErrorAction SilentlyContinue)) {
  throw "REPO_ROOT must be the surveyed target repository root before checking inherited policy."
}
$repoRootFullPath = [IO.Path]::GetFullPath($REPO_ROOT)
if (-not (Test-Path -LiteralPath $repoRootFullPath -PathType Container)) {
  throw "REPO_ROOT is not an existing directory."
}

function Assert-NotRepositoryLink([System.IO.FileSystemInfo]$Item) {
  $linkTypeProperty = $Item.PSObject.Properties["LinkType"]
  $hasLinkType = $null -ne $linkTypeProperty -and
    -not [string]::IsNullOrWhiteSpace([string]$linkTypeProperty.Value)
  $isReparsePoint = ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
  if ($hasLinkType -or $isReparsePoint) {
    throw "Repository survey refuses linked or reparse-point path '$($Item.FullName)'."
  }
}

$repoRootItem = Get-Item -LiteralPath $repoRootFullPath -Force
Assert-NotRepositoryLink $repoRootItem
$repoRootTrimmed = $repoRootFullPath.TrimEnd(
  [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
)
$repoRootBoundary = $repoRootTrimmed + [IO.Path]::DirectorySeparatorChar
$pathComparison = if (
  [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
) {
  [StringComparison]::OrdinalIgnoreCase
} else {
  [StringComparison]::Ordinal
}

function Assert-RepositorySurveyPath([string]$Candidate) {
  $candidateFullPath = [IO.Path]::GetFullPath($Candidate)
  $insideRoot = $candidateFullPath.Equals(
    $repoRootFullPath,
    $pathComparison
  ) -or $candidateFullPath.StartsWith(
    $repoRootBoundary,
    $pathComparison
  )
  if (-not $insideRoot) {
    throw "Repository survey path escapes REPO_ROOT: '$candidateFullPath'."
  }

  $currentPath = $repoRootFullPath
  if (-not $candidateFullPath.Equals(
    $repoRootFullPath,
    $pathComparison
  )) {
    $relativePath = $candidateFullPath.Substring($repoRootBoundary.Length)
    foreach ($component in $relativePath.Split(
      [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar),
      [StringSplitOptions]::RemoveEmptyEntries
    )) {
      $currentPath = Join-Path $currentPath $component
      if (-not (Test-Path -LiteralPath $currentPath)) { break }
      Assert-NotRepositoryLink (Get-Item -LiteralPath $currentPath -Force)
    }
  }
  return $candidateFullPath
}

function Get-RepositorySurveyFile([string]$Directory, [switch]$Recurse) {
  $safeDirectory = Assert-RepositorySurveyPath $Directory
  if (-not (Test-Path -LiteralPath $safeDirectory)) { return @() }
  if (-not (Test-Path -LiteralPath $safeDirectory -PathType Container)) {
    throw "Repository survey directory is not a directory: '$safeDirectory'."
  }

  $pending = [Collections.Generic.Stack[string]]::new()
  $files = [Collections.Generic.List[System.IO.FileInfo]]::new()
  $pending.Push($safeDirectory)
  while ($pending.Count -gt 0) {
    $currentDirectory = $pending.Pop()
    foreach ($item in @(Get-ChildItem -LiteralPath $currentDirectory -Force)) {
      Assert-NotRepositoryLink $item
      if ($item.PSIsContainer) {
        if ($Recurse) { $pending.Push($item.FullName) }
      } else {
        $files.Add($item)
      }
    }
  }
  return $files.ToArray()
}

$localIssueTemplateDirectory = Assert-RepositorySurveyPath (
  Join-Path $repoRootFullPath ".github/ISSUE_TEMPLATE"
)
$localIssueTemplateFiles = @()
if (Test-Path -LiteralPath $localIssueTemplateDirectory -PathType Container) {
  $localIssueTemplateFiles = @(
    Get-RepositorySurveyFile -Directory $localIssueTemplateDirectory -Recurse
  )
}
$localPullRequestTemplateFiles = @()
foreach ($directory in @(".github", ".", "docs")) {
  $directoryPath = Assert-RepositorySurveyPath (Join-Path $repoRootFullPath $directory)
  if (Test-Path -LiteralPath $directoryPath -PathType Container) {
    $localPullRequestTemplateFiles += @(
      Get-RepositorySurveyFile -Directory $directoryPath | Where-Object {
        $_.Name -match '(?i)^PULL_REQUEST_TEMPLATE(?:\..+)?$'
      }
    )
  }
}
foreach ($directory in @(
  ".github/PULL_REQUEST_TEMPLATE",
  "PULL_REQUEST_TEMPLATE",
  "docs/PULL_REQUEST_TEMPLATE"
)) {
  $directoryPath = Assert-RepositorySurveyPath (Join-Path $repoRootFullPath $directory)
  if (Test-Path -LiteralPath $directoryPath -PathType Container) {
    $localPullRequestTemplateFiles += @(
      Get-RepositorySurveyFile -Directory $directoryPath -Recurse
    )
  }
}

# The profile exposes effective community files for non-forks. Keep the full
# files object so source URLs can reveal an inherited OWNER/.github policy.
$effectiveCommunityFiles = $null
if (-not $target.isFork) {
  $profileOutput = gh api --hostname github.com "repos/$owner/$repo/community/profile" 2>&1
  if ($LASTEXITCODE -eq 0) {
    $effectiveCommunityFiles = (($profileOutput | Out-String) | ConvertFrom-Json).files
  } else {
    Write-Warning "Could not inspect effective community-profile files; do not claim that no inherited policy exists. $($profileOutput | Out-String)"
  }
}

$inheritedPaths = @()
$defaults = $null
if ($repo -ne ".github") {
  $defaultsOutput = gh repo view "github.com/$owner/.github" --json visibility,defaultBranchRef 2>&1
  if ($LASTEXITCODE -eq 0) {
    $defaults = ($defaultsOutput | Out-String) | ConvertFrom-Json
    if ($defaults.visibility -eq "PUBLIC" -and $null -ne $defaults.defaultBranchRef) {
      $encodedDefaultsBranch = [Uri]::EscapeDataString($defaults.defaultBranchRef.name)
      $treeOutput = gh api --hostname github.com `
        "repos/$owner/.github/git/trees/${encodedDefaultsBranch}?recursive=1" 2>&1
      if ($LASTEXITCODE -eq 0) {
        $defaultsTree = ($treeOutput | Out-String) | ConvertFrom-Json
        if ($defaultsTree.truncated) {
          Write-Warning "The $owner/.github tree response was truncated; inspect supported paths directly before claiming no inherited policy exists."
        }
        $inheritedPaths = @(
          $defaultsTree.tree | Where-Object type -eq "blob" | ForEach-Object { $_.path }
        )
      } else {
        Write-Warning "The $($defaults.visibility.ToLowerInvariant()) $owner/.github repository exists, but its default files could not be inspected. $($treeOutput | Out-String)"
      }
    }
  } elseif (($defaultsOutput | Out-String) -notmatch '(?is)HTTP 404|Could not resolve to a Repository') {
    Write-Warning "Could not determine whether $owner/.github supplies inherited policy; do not claim that none exists. $($defaultsOutput | Out-String)"
  }
}

$inheritedPathSet = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($path in $inheritedPaths) { [void]$inheritedPathSet.Add($path.Replace('\', '/')) }

$inheritedDefaultFiles = [ordered]@{}
foreach ($name in @(
  "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "FUNDING.yml", "GOVERNANCE.md",
  "SECURITY.md", "SUPPORT.md"
)) {
  foreach ($candidate in @(".github/$name", $name, "docs/$name")) {
    if ($inheritedPathSet.Contains($candidate)) {
      $inheritedDefaultFiles[$name] = $candidate
      break
    }
  }
}
$defaultsArePublic = $null -ne $defaults -and $defaults.visibility -eq "PUBLIC"
if ($defaultsArePublic) {
  foreach ($prefix in @(".github/", "", "docs/")) {
    $candidate = $inheritedPaths | Where-Object {
      $_ -match "(?i)^$([regex]::Escape($prefix))PULL_REQUEST_TEMPLATE(?:\..+)?$"
    } | Select-Object -First 1
    if ($null -ne $candidate) {
      $inheritedDefaultFiles["PULL_REQUEST_TEMPLATE"] = $candidate
      break
    }
  }
}
$availableDefaultIssueTemplates = @(
  if ($defaultsArePublic) {
    $inheritedPaths | Where-Object { $_ -match '(?i)^\.github/ISSUE_TEMPLATE/.+' }
  }
)
$inheritedIssueTemplates = if ($localIssueTemplateFiles.Count -eq 0) {
  @($availableDefaultIssueTemplates)
} else {
  @()
}
$availableDefaultPullRequestTemplates = @(
  if ($inheritedDefaultFiles.Contains("PULL_REQUEST_TEMPLATE")) {
    $inheritedDefaultFiles["PULL_REQUEST_TEMPLATE"]
  }
  if ($defaultsArePublic) {
    $inheritedPaths | Where-Object {
      $_ -match '(?i)^(?:\.github/|docs/)?PULL_REQUEST_TEMPLATE/.+'
    }
  }
)
$inheritedPullRequestTemplates = if ($localPullRequestTemplateFiles.Count -eq 0) {
  @($availableDefaultPullRequestTemplates)
} else {
  # A local single or multi-template source suppresses account-default PR templates.
  [void]$inheritedDefaultFiles.Remove("PULL_REQUEST_TEMPLATE")
  @()
}
$inheritedDiscussionForms = @(
  $inheritedPaths | Where-Object { $_ -match '(?i)^\.github/DISCUSSION_TEMPLATE/.+' }
)
```

Đối chiếu profile URL hiệu lực với target repository và `$owner/.github`; báo
riêng `$inheritedDefaultFiles`, `$inheritedIssueTemplates`,
`$inheritedPullRequestTemplates`, `$inheritedDiscussionForms` với source cục bộ.
Coi `$availableDefaultIssueTemplates` và
`$availableDefaultPullRequestTemplates` chỉ là account default, không phải
inheritance hiệu lực. Khi danh sách template cục bộ tương ứng không rỗng, giải
thích vì sao account default bị suppress và đánh giá riêng source cục bộ. Nếu
file không có ở target nhưng đang được kế thừa hiệu lực, hỏi trước khi tạo
override local; đồng ý scaffold file nói chung không đồng nghĩa được phép thay
policy toàn account.

Nếu `$inheritedIssueTemplates` không rỗng, lấy từng template hiệu lực từ
`$owner/.github` và parse metadata trước khi cấu hình label. Với issue form, parse
YAML field `labels`; với Markdown template legacy, chỉ parse YAML front matter
và normalize giá trị `labels`. Không scrape label từ body text template. Ghi tên
label thu được vào `$effectiveInheritedIssueLabels`. Label được tham chiếu chỉ
hoạt động khi tồn tại ở cả `$owner/.github` và target. Báo label account default
thiếu ở `$owner/.github` là default bị hỏng; không sửa repository riêng đó khi
chưa có xác nhận rõ. Nếu label có ở `$owner/.github` nhưng thiếu ở target, sau
xác nhận hãy copy đúng name, color và description đã xác nhận sang target.
Không bịa metadata label hoặc tuyên bố inherited template hoạt động đầy đủ cho
đến khi xác minh cả hai repository.

## Branch protection (classic)

Chỉ bật nếu người dùng muốn thực thi quy trình PR. Bắt buộc PR, các check đã
chọn phải đạt, branch phải up-to-date và áp dụng cả admin. Owner làm một mình
vẫn có thể tự merge (0 approval bắt buộc). Dùng default branch đã phát hiện,
không thay bằng tên cố định.

Trên GitHub.com, private repository chỉ dùng protected branch khi có GitHub Pro,
Team hoặc Enterprise Cloud. Với private/internal repository, xác nhận entitlement
trước mutation nếu có thể. Nếu không, coi `403` là forbidden và giữ `404` là
repository/branch thiếu hoặc không truy cập được, trừ khi evidence riêng chứng
minh giới hạn plan. Tiếp tục scaffold mà không tuyên bố đã bật protection.

Lập check list từ context đã xác minh trong lần scaffold, không từ workflow
filename:

- Chỉ thêm `ci-success` khi repo-scaffold CI asset được tạo hoặc CI workflow có
  sẵn được duyệt cập nhật và xác minh phát aggregate check đó.
- Chỉ thêm `dependency-review` khi asset repo-scaffold tương ứng được tạo cho
  repository đủ điều kiện.
- Chỉ thêm `commitlint` khi workflow asset repo-scaffold tương ứng được tạo.
- Với workflow hiện có không sửa, kiểm tra `jobs` mapping và job-level `name`
  tùy chọn, rồi hỏi user aggregate context thật nào cần bắt buộc. Filename khớp
  không chứng minh check cụ thể tồn tại.
- Trước mutation, tính producer của mọi candidate required context trong
  workflow set cuối: dùng job-level `name` nếu có, nếu không dùng job ID; resolve
  matrix/reusable-workflow name thành context GitHub thật sự emit. GitHub không
  scope required check theo workflow/matrix/event, nên mỗi context chỉ được có
  đúng một producer.
- Xác minh event coverage, không chỉ context name. Producer phải chạy mọi
  `pull_request`, không workflow-level `paths`, `paths-ignore` hoặc branch filter
  nào suppress được toàn workflow. Khi effective merge queue áp dụng, cũng phải
  chạy `merge_group` có `checks_requested`. Job-level `if` phải true và chạy
  validation thật trên mọi event liên quan; GitHub báo job skip là success,
  không phải bằng chứng gate đã chạy. Tên trùng ngoài required set không chặn
  protection.
- Với repo-scaffold asset mới, parse YAML cuối và ghi coverage trực tiếp. Với
  check không đổi, dynamic, matrix name, reusable hoặc do bên ngoài cung cấp,
  xác minh run PR đại diện (và merge-group nếu áp dụng), nếu không thì đừng bắt
  buộc.
- Với mỗi PR đại diện, lấy cả `.head.sha` và `.merge_commit_sha` hiện tại bằng
  lệnh `gh api` trong bản tiếng Anh. Cần PR đại diện mergeable và test-merge SHA
  khác null. Kiểm tra đầy đủ trang Check Runs/Commit Statuses trên cả hai SHA,
  thêm merge-group SHA nếu áp dụng và SHA nhánh mặc định gần đây. Nếu test-merge
  SHA có bất kỳ Check Run hoặc Commit Status nào, nó điều khiển: yêu cầu context
  và source đã chọn trên SHA đó, không fallback sang kết quả head-only. Nếu không
  có cả hai, dùng head SHA. Check phải hoàn tất thành công trong repository này
  trong 7 ngày gần đây trên controlling SHA mới được chọn. So context name không
  phân biệt hoa thường. Nếu Check Run và Commit Status cùng tên required, GitHub
  cần cả hai; loại candidate đó thay vì coi là một producer. Ghi đúng `app.id`
  dương của Check Run mong muốn trên controlling SHA; nhiều PR đại diện cần App
  ID nhất quán. Bind required check mới với App ID đó, không để GitHub tự chọn
  source gần nhất.
- Khi effective merge queue áp dụng, truyền `merge_group` SHA thành công gần
  đây cho preflight. Nó xác minh cùng workflow blob/context chạy trên SHA đó,
  Actions run có event chính xác `merge_group` và GitHub App ID khớp PR check đã
  chọn. Không có merge-group SHA đã xác minh thì không mutation protection.
- Dừng trước khi áp required-status-check protection nếu chưa xác nhận gate thật.
  Không gửi context mà workflow không emit.

Structural producer check từ chối conditional step và `continue-on-error`.
Ngoại lệ duy nhất cho step condition là checkout guard đã phát hành, bỏ qua PR
được xác nhận đã đóng. Điều kiện literal này checkout mọi PR đang mở và merge
group; phải dùng `actions/checkout` đã pin với đúng head SHA event và tắt
credential persistence, không được guard `run` step. Actor filter, điều kiện
state đảo và checkout ref chưa xác minh là inconclusive. Closed-PR
not-applicable không chứng nhận gate cho PR mở.

Chọn exact required context trước preflight. Dùng cùng list và App ID trả về cho
mutation; không dựng lại list hoặc nhập producer evidence lần nữa sau đó.

Bundled `scripts/branch_protection_preflight.py` biến proof này thành gate chỉ
đọc, fail-closed. Chạy sau khi workflow cuối đã push lên PR đại diện đang mở,
mergeable và trước mọi branch-protection mutation. Script đọc exact workflow
blob là regular file từ test-merge commit PR. GitHub chạy `pull_request` workflow
từ merge commit (xem [event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)).

Preflight từ chối YAML key trùng và producer nhập nhằng; xác minh `pull_request`
coverage không lọc (hoặc trusted `pull_request_target` producer từ default
branch đã xác minh), cùng `merge_group` khi effective merge queue áp dụng. Cần
job executable không conditional và Check Run hoàn tất thành công trong 7 ngày
trên controlling SHA: test-merge SHA nếu có Check Run/Commit Status, nếu không
dùng PR head. Lấy GitHub App ID từ cùng controlling SHA và từ chối Commit Status
trùng tên ở đó. Khi queue áp dụng, cần Check Run `merge_group` thành công gần đây
trên SHA truyền vào, cùng workflow blob/context/App ID; Actions run phải báo
event chính xác `merge_group`.

Mỗi Check Run nối `check_suite.id` với đúng một Actions workflow run, rồi xác
minh commit, workflow path và event của run đó. `workflow_dispatch` thành công
không thay được required-check event hợp lệ. Fine-grained token cần Actions: read
để đọc workflow run; evidence không truy cập được hoặc thiếu là inconclusive.

Với closed-PR lifecycle name đã phát hành, producer discovery chỉ resolve
fallback literal dùng cho PR mở và merge group. Closed noop có tên khác, không
required và không được cấp success cho context thường. Dynamic name khác không
hỗ trợ. `workflow_run.pull_requests` liệt kê open head/branch khớp, không nhất
thiết PR đã kích hoạt run; đừng coi đó là trigger payload hoặc workflow-blob
attestation. Event-specific provenance ngoài path/SHA check hiện có phải xác
minh riêng trước khi nói PR cụ thể đã validated.

Workflow inspection xác minh bounded raw UTF-8 byte với advertised GitHub SHA-1
blob ID trước parse YAML/xác định producer. Giữ byte chính xác, gồm CRLF, BOM,
Unicode; không hash text đã normalize. Mismatch/object ID không hỗ trợ là
inconclusive. Content binding không attestation workflow definition đã thực thi.

Root Git-tree inventory giới hạn mọi entry trước phân loại; từ chối non-object
entry hoặc path thiếu/rỗng/sai type trước blob read. Entry đó không phân loại
được, không phải bằng chứng file không liên quan. Direct-file policy vẫn loại
path non-workflow đã biết. Selected Check Run phải báo tường minh `status:
completed`, `conclusion: success`, `name` là string thật khớp required context.
Không stringify null/Boolean/number/collection thành context. Conclusion thành
công hoặc timestamp hoàn tất không thay được state; receipt thiếu, malformed,
queued/in-progress là inconclusive, không cho phép protection.

Với `pull_request_target`, exact workflow blob còn phải khớp tại PR base commit
vì GitHub chạy event từ default branch của base repository. Merge workflow target
mới/sửa trước preflight. Script không sửa GitHub state; nó bind đúng repository/
default branch, từ chối target archived/disabled, yêu cầu admin permission hiện
tại. API, parse, pagination, mergeability hoặc evidence gap nào cũng inconclusive;
cấm required-check mutation.

Truyền numeric repository ID discovery ban đầu, không seed ID mới. Preflight
bind base repository ID/PR number đại diện; revalidate controlling head/base/
test-merge/state, effective rule và trusted default-branch commit nếu áp dụng,
rồi đọc lại repository active/admin/branch state trước verdict. Drift là
inconclusive. Không có discovery ID thì inspection chỉ discovery, không cấp
quyền protection.

Resolve `REPO_SCAFFOLD_SKILL_ROOT` về thư mục cài/source chứa `SKILL.md` của
skill này rồi chạy:
Kiểm tra mỗi PR đại diện bằng cách lấy head.sha và merge_commit_sha hiện
tại; cần PR mergeable, test-merge SHA khác null. Đọc đủ trang Check Runs và
Commit Statuses trên cả hai SHA, merge-group SHA nếu cần và SHA default branch
gần đây. Nếu test-merge SHA có check/status, nó là controlling SHA; không fallback
sang head-only pass. Nếu không có, dùng head SHA. Check chỉ được bắt buộc khi
hoàn tất thành công trong repository này trong bảy ngày gần nhất trên controlling
SHA. So context không phân biệt hoa thường. Check Run và Commit Status trùng tên
không được coi là một producer vì GitHub yêu cầu cả hai. Ghi exact app.id dương
của Check Run trên controlling SHA; nhiều PR đại diện phải thống nhất App ID và
mọi required check mới phải bind vào App ID đó.

Khi merge queue áp dụng, yêu cầu merge_group SHA thành công gần đây; xác minh
cùng workflow blob/context, event chính xác merge_group và GitHub App ID khớp PR
check. Không có SHA hợp lệ thì không đổi branch protection. Không áp required
status check nếu chưa xác nhận gate thật; không submit context workflow không
phát ra.

Structural producer check từ chối conditional step và continue-on-error. Ngoại
lệ duy nhất là checkout guard đã phát hành, bỏ qua PR đã đóng rõ ràng. Nó phải
checkout mọi PR mở/merge group bằng actions/checkout đã pin, exact head SHA
event, credential persistence tắt; không guard run step. Actor filter, điều kiện
state đảo và checkout ref chưa xác minh là inconclusive. Closed-PR
not-applicable không chứng minh gate cho PR mở.

Chọn exact required context trước preflight, giữ nguyên list và App ID trả về cho
mutation. Preflight chỉ đọc, fail-closed; chạy sau khi push workflow cuối lên PR
đại diện đang mở/mergeable. Nó đọc regular workflow blob từ test-merge commit vì
GitHub chạy pull_request workflow từ merge commit (xem [event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)).
Nó từ chối YAML key trùng/producer nhập nhằng, xác minh pull_request coverage
không lọc (hoặc trusted pull_request_target từ default branch) và merge_group
khi queue áp dụng; cần job executable không điều kiện cùng Check Run thành công
tối đa bảy ngày trên controlling SHA.

Mỗi Check Run phải bind check_suite.id với đúng một Actions workflow run, rồi
xác minh commit/path/event; workflow_dispatch không thay required-check event
hợp lệ. Fine-grained token cần Actions: read; evidence thiếu/không truy cập được
là inconclusive. Với closed-PR lifecycle name đã phát hành, chỉ resolve literal
fallback cho PR mở/merge group; closed noop có tên non-required riêng, không cấp
success cho context thường. Dynamic name khác không hỗ trợ.
workflow_run.pull_requests chỉ liệt kê open head/branch khớp, không nhất thiết PR
đã trigger run; không coi là trigger payload hoặc workflow attestation.

Trước parse YAML, xác minh bounded raw UTF-8 bytes với advertised GitHub SHA-1
blob ID; giữ CRLF/BOM/Unicode, không normalize. Mismatch/object ID không hỗ trợ
là inconclusive; content binding không attestation workflow đã chạy. Root
Git-tree inventory giới hạn trước phân loại và từ chối non-object/path
thiếu/rỗng/sai type trước blob read; entry không phân loại được không chứng minh
file không liên quan. Check Run cần status completed, conclusion success và name
string khớp required context; không stringify null/Boolean/number/collection.
Receipt thiếu, malformed, queued/in-progress không cho phép protection.

Với pull_request_target, exact workflow blob phải khớp PR base commit vì GitHub
chạy event từ default branch của base repo. Merge workflow target mới/sửa trước
preflight. Script không đổi GitHub state; bind repository/default branch, từ
chối archived/disabled và yêu cầu admin hiện tại. API/parse/pagination/
mergeability/evidence gap là inconclusive.
Truyền numeric repository ID từ discovery ban đầu, không seed ID mới. Preflight
bind base repository ID và PR number đại diện; revalidate controlling
head/base/test-merge/state, effective rule và commit default branch đáng tin khi
áp dụng; sau đó đọc lại repository active/admin/branch state trước verdict. Drift
là inconclusive. Không có discovery ID thì inspection chỉ discovery, không cho
phép protection.

Resolve `REPO_SCAFFOLD_SKILL_ROOT` tới thư mục cài/source chứa `SKILL.md` của
skill này, rồi chạy:

```powershell
$defaultBranch = $repoView.defaultBranchRef.name
if ([string]::IsNullOrWhiteSpace($defaultBranch)) {
  throw "The repository has no default branch; confirm one before configuring protection."
}
# Replace this example with every context selected above. For an unchanged
# workflow, add only the context the user confirmed from its real job.
$requiredCheckNames = @("ci-success")
$mergeGroupSha = $null # Set to the verified recent merge_group SHA when a queue applies.
if ($requiredCheckNames.Count -eq 0) {
  throw "No verified required-check context; stop before configuring protection."
}
$branchProtectionPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/branch_protection_preflight.py"
if (-not (Test-Path -LiteralPath $branchProtectionPreflight -PathType Leaf)) {
  throw "The bundled branch-protection preflight script is missing; do not mutate protection."
}
$preflightArguments = @(
  "--repository", "OWNER/REPO",
  "--expected-repository-id", $SELECTED_REPOSITORY_ID,
  "--default-branch", $defaultBranch,
  "--pull-request", "NUMBER"
)
foreach ($context in $requiredCheckNames) {
  $preflightArguments += @("--required-check", [string]$context)
}
if ($null -ne $mergeGroupSha) {
  $preflightArguments += @("--merge-group-sha", [string]$mergeGroupSha)
}
function Assert-RequiredCheckPreflightSchema {
  param([object]$Verdict)
  if ($Verdict -isnot [pscustomobject]) { throw "Required-check verdict must be a JSON object." }
  Assert-SelectedRepositoryId -RepositoryId $Verdict.repository_id
  foreach ($field in @("inspection_complete", "administration_permission", "merge_queue_required")) {
    if ($Verdict.$field -isnot [bool]) { throw "Required-check verdict has an invalid Boolean field." }
  }
  if (-not $Verdict.administration_permission) { throw "Administration permission is not confirmed." }
  foreach ($field in @("repository", "default_branch", "decision")) {
    if ($Verdict.$field -isnot [string] -or [string]::IsNullOrWhiteSpace($Verdict.$field)) {
      throw "Required-check verdict has an invalid text field."
    }
  }
  if (($Verdict.pull_request -isnot [int] -and $Verdict.pull_request -isnot [long]) -or
      $Verdict.pull_request -le 0) { throw "Required-check verdict has an invalid PR number." }
  foreach ($field in @("head_sha", "test_merge_sha")) {
    if ($Verdict.$field -isnot [string] -or $Verdict.$field -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') {
      throw "Required-check verdict has an invalid controlling commit."
    }
  }
  if ($null -eq $Verdict.PSObject.Properties['merge_group_sha'] -or
      ($Verdict.merge_queue_required -and
       ($Verdict.merge_group_sha -isnot [string] -or $Verdict.merge_group_sha -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$')) -or
      (-not $Verdict.merge_queue_required -and $null -ne $Verdict.merge_group_sha)) {
    throw "Required-check verdict has an invalid queue/commit binding."
  }
  if ($Verdict.required_checks -isnot [array] -or $Verdict.required_checks.Count -lt 1 -or
      $Verdict.required_checks.Count -gt 50) { throw "Required-check inventory is missing or out of bounds." }
  $contexts = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach ($check in $Verdict.required_checks) {
    if ($check -isnot [pscustomobject] -or $check.context -isnot [string] -or
        $check.context.Length -lt 1 -or $check.context.Length -gt 256 -or
        $check.context -match '[\r\n\x00]' -or -not $contexts.Add($check.context) -or
        ($check.app_id -isnot [int] -and $check.app_id -isnot [long]) -or $check.app_id -le 0 -or
        $check.producer -isnot [string] -or [string]::IsNullOrWhiteSpace($check.producer)) {
      throw "Required-check context, App ID or producer is malformed or duplicated."
    }
  }
}

$preflightOutput = python $branchProtectionPreflight @preflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Required-check evidence is inconclusive; do not mutate protection. $($preflightOutput | Out-String)"
}
$requiredCheckPreflight = ($preflightOutput | Out-String) | ConvertFrom-Json
Assert-RequiredCheckPreflightSchema -Verdict $requiredCheckPreflight
if ($requiredCheckPreflight.inspection_complete -isnot [bool] -or
    -not $requiredCheckPreflight.inspection_complete -or
    $requiredCheckPreflight.decision -ne "may-configure-classic-protection" -or
    $requiredCheckPreflight.merge_queue_required -isnot [bool]) {
  throw "Required-check evidence is incomplete; do not mutate protection."
}
if ($requiredCheckPreflight.repository -cne "OWNER/REPO" -or
    $requiredCheckPreflight.default_branch -cne $defaultBranch -or
    $requiredCheckPreflight.merge_queue_required -ne ($null -ne $mergeGroupSha) -or
    $requiredCheckPreflight.merge_group_sha -cne $mergeGroupSha) {
  throw "Branch-protection preflight input does not match the protection mutation."
}
$verifiedChecks = @($requiredCheckPreflight.required_checks)
$verifiedCheckNames = @($verifiedChecks | ForEach-Object { [string]$_.context })
if ($verifiedCheckNames.Count -ne $requiredCheckNames.Count -or
    $null -ne (Compare-Object `
      -ReferenceObject @($requiredCheckNames | Sort-Object) `
      -DifferenceObject @($verifiedCheckNames | Sort-Object))) {
  throw "Required-check preflight contexts differ from the mutation plan; rerun with the exact context list."
}
# Use only these returned values in the mutation block. Do not add contexts or
# substitute app IDs manually after the preflight completes.
$requiredCheckNames = $verifiedCheckNames
$requiredAppIdsByContext = [Collections.Generic.Dictionary[string, int64]]::new(
  [StringComparer]::OrdinalIgnoreCase
)
foreach ($check in $verifiedChecks) {
  if ([string]::IsNullOrWhiteSpace([string]$check.context) -or
      [int64]$check.app_id -le 0) {
    throw "Required-check preflight returned an invalid context or App ID."
  }
  $requiredAppIdsByContext[[string]$check.context] = [int64]$check.app_id
}

function Assert-FreshRequiredCheckPreflight {
  $output = python $branchProtectionPreflight @preflightArguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    throw "Final required-check preflight is inconclusive; do not mutate protection. $($output | Out-String)"
  }
  try {
    $fresh = ($output | Out-String) | ConvertFrom-Json
  } catch {
    throw "Final required-check preflight returned invalid JSON; do not mutate protection."
  }
  Assert-RequiredCheckPreflightSchema -Verdict $fresh
  if ($fresh.inspection_complete -isnot [bool] -or
      -not $fresh.inspection_complete -or
      $fresh.decision -ne "may-configure-classic-protection" -or
      $fresh.repository -cne "OWNER/REPO" -or
      $fresh.default_branch -cne $defaultBranch -or
      [int]$fresh.pull_request -ne [int]$requiredCheckPreflight.pull_request -or
      ([string]$fresh.head_sha).ToLowerInvariant() -cne
        ([string]$requiredCheckPreflight.head_sha).ToLowerInvariant() -or
      ([string]$fresh.test_merge_sha).ToLowerInvariant() -cne
        ([string]$requiredCheckPreflight.test_merge_sha).ToLowerInvariant() -or
      $fresh.merge_queue_required -isnot [bool] -or
      $fresh.merge_queue_required -ne $requiredCheckPreflight.merge_queue_required -or
      $fresh.merge_group_sha -cne $requiredCheckPreflight.merge_group_sha) {
    throw "Representative pull-request evidence or target changed since preflight; review the fresh result before mutating protection."
  }
  $approvedBindings = @($verifiedChecks | Sort-Object context | ForEach-Object {
    [ordered]@{ context = [string]$_.context; app_id = [int64]$_.app_id; producer = [string]$_.producer }
  })
  $freshBindings = @($fresh.required_checks | Sort-Object context | ForEach-Object {
    [ordered]@{ context = [string]$_.context; app_id = [int64]$_.app_id; producer = [string]$_.producer }
  })
  if ((ConvertTo-Json -InputObject $approvedBindings -Compress -Depth 4) -cne
      (ConvertTo-Json -InputObject $freshBindings -Compress -Depth 4)) {
    throw "Required-check contexts or App IDs changed since preflight; review the fresh result before mutating protection."
  }
  return $fresh
}
```

Truyền mọi context vào block mutation về sau. Ngay trước protection write đầu,
ví dụ chạy lại preflight và yêu cầu repository, branch, PR head, test-merge SHA,
context, App ID không đổi. Nếu nhiều context đủ điều kiện, truyền một
`--required-check` cho từng context, giữ nguyên cặp context/App ID trả về. Nếu
evidence đổi, review kết quả mới và chạy lại quy trình trước mutation.
Ví dụ PowerShell dưới đây giải thích cách giữ và xác minh classic-protection
setting sau mutation; không bypass preflight bằng cách tự điền producer evidence.

Ví dụ PowerShell:

```powershell
# Read repository metadata as command output. Never paste a detected branch into
# PowerShell source: valid branch names may contain `$`, `;`, quotes, or parentheses.
$repoViewOutput = gh repo view github.com/OWNER/REPO --json nameWithOwner,defaultBranchRef,visibility
if ($LASTEXITCODE -ne 0) { throw "Failed to read repository metadata." }
$repoView = $repoViewOutput | ConvertFrom-Json
$owner, $repo = $repoView.nameWithOwner -split '/', 2
$defaultBranch = $repoView.defaultBranchRef.name
if ([string]::IsNullOrWhiteSpace($defaultBranch)) {
  throw "The repository has no default branch; confirm one before configuring protection."
}
if ($repoView.nameWithOwner -cne $requiredCheckPreflight.repository -or
    $defaultBranch -cne $requiredCheckPreflight.default_branch) {
  throw "Repository identity or default branch changed after preflight; rerun before mutating protection."
}
$encodedBranch = [Uri]::EscapeDataString($defaultBranch)

# Required checks need merge-group coverage whenever an effective queue rule applies.
$effectiveRuleOutput = gh api --hostname github.com --paginate `
  "repos/$owner/$repo/rules/branches/$encodedBranch" `
  --jq '.[] | select(.type == "merge_queue") | .type' 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Could not determine whether a merge queue applies; stop before configuring required checks. $($effectiveRuleOutput | Out-String)"
}
$hasMergeQueue = @($effectiveRuleOutput | Where-Object { $_ -eq "merge_queue" }).Count -gt 0
if ($hasMergeQueue -ne [bool]$requiredCheckPreflight.merge_queue_required) {
  throw "Effective merge-queue rules changed after preflight; rerun before mutating protection."
}
foreach ($context in $requiredCheckNames) {
  if (-not $requiredAppIdsByContext.ContainsKey($context)) {
    throw "Required-check preflight did not return a verified App ID for $context."
  }
}

function ConvertTo-BranchProtectionAppId([object]$Value) {
  if ($null -eq $Value) { return -1L }
  if (($Value -is [int] -or $Value -is [long]) -and [int64]$Value -gt 0) {
    return [int64]$Value
  }
  throw "Branch-protection App ID must be a positive integer or null."
}

function Get-BranchProtectionReviewState([object]$Protection) {
  if ($null -eq $Protection -or $Protection -isnot [pscustomobject]) {
    throw "Branch-protection response is incomplete; stop before mutation."
  }
  $reviewProperty = $Protection.PSObject.Properties['required_pull_request_reviews']
  if ($null -eq $reviewProperty -or $null -eq $reviewProperty.Value) {
    return $null
  }
  if ($reviewProperty.Value -isnot [pscustomobject]) {
    throw "Pull-request review protection is malformed; stop before mutation."
  }
  return $reviewProperty.Value
}

function Assert-ClassicProtectionState(
  [object]$Protection,
  [Collections.Generic.IDictionary[string, int64]]$ExpectedAppIdsByContext,
  [object]$ExpectedReviewApprovalCount = $null
) {
  $problems = @()
  $null = Get-RequiredStatusCheckSnapshot $Protection
  if ($Protection.required_status_checks.strict -isnot [bool] -or
      -not $Protection.required_status_checks.strict) {
    $problems += "strict status checks are disabled"
  }
  if ($Protection.enforce_admins.enabled -isnot [bool] -or
      -not $Protection.enforce_admins.enabled) {
    $problems += "administrator enforcement is disabled"
  }
  $reviewState = Get-BranchProtectionReviewState $Protection
  if ($null -eq $reviewState) {
    $problems += "pull request protection is absent"
  } elseif ($null -ne $ExpectedReviewApprovalCount) {
    $actualReviewApprovalCount = $reviewState.required_approving_review_count
    if (($actualReviewApprovalCount -isnot [int] -and
        $actualReviewApprovalCount -isnot [long]) -or
        [int64]$actualReviewApprovalCount -ne [int64]$ExpectedReviewApprovalCount) {
      $problems += "pull request approval count differs from the requested state"
    }
  }

  $actualAppIdsByContext = [Collections.Specialized.OrderedDictionary]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($check in @($Protection.required_status_checks.checks)) {
    if ($null -eq $check -or [string]::IsNullOrWhiteSpace($check.context)) {
      continue
    }
    try {
      $appId = ConvertTo-BranchProtectionAppId $check.app_id
    } catch {
      $problems += "$($check.context) has an invalid app binding"
      continue
    }
    if ($actualAppIdsByContext.Contains($check.context) -and
        [int64]$actualAppIdsByContext[$check.context] -ne $appId) {
      $problems += "$($check.context) has conflicting app bindings"
    } else {
      $actualAppIdsByContext[$check.context] = $appId
    }
  }
  foreach ($context in @($Protection.required_status_checks.contexts)) {
    if (-not [string]::IsNullOrWhiteSpace($context) -and
        -not $actualAppIdsByContext.Contains($context)) {
      $actualAppIdsByContext[$context] = -1L
    }
  }

  foreach ($context in @($ExpectedAppIdsByContext.Keys)) {
    if (-not $actualAppIdsByContext.Contains($context)) {
      $problems += "$context is missing"
    } elseif ([int64]$actualAppIdsByContext[$context] -ne
        [int64]$ExpectedAppIdsByContext[$context]) {
      $problems += "$context has an unexpected app binding"
    }
  }
  foreach ($context in @($actualAppIdsByContext.Keys)) {
    if (-not $ExpectedAppIdsByContext.ContainsKey($context)) {
      $problems += "$context is unexpectedly required"
    }
  }
  if ($problems.Count -gt 0) {
    throw "Classic branch protection did not reach the requested state: $($problems -join '; ')."
  }
}

$protectionPath = "repos/$owner/$repo/branches/$encodedBranch/protection"

function Get-RequiredStatusCheckSnapshot([object]$Protection) {
  if ($null -eq $Protection -or $Protection -isnot [pscustomobject]) {
    throw "Branch-protection response is incomplete; stop before mutation."
  }
  $statusProperty = $Protection.PSObject.Properties['required_status_checks']
  # The top-level property is optional when no required status checks are set.
  if ($null -eq $statusProperty) { return "null" }
  $statusChecks = $statusProperty.Value
  if ($null -eq $statusChecks) { return "null" }
  if ($statusChecks -isnot [pscustomobject]) {
    throw "Required-status-check state is incomplete; stop before mutation."
  }
  # The GET schema makes strict optional; the PATCH below intentionally sets it.
  $strictProperty = $statusChecks.PSObject.Properties['strict']
  $strictSnapshot = $null
  if ($null -ne $strictProperty) {
    if ($strictProperty.Value -isnot [bool]) {
      throw "Required-status-check strictness is invalid; stop before mutation."
    }
    $strictSnapshot = [bool]$strictProperty.Value
  }
  $checksProperty = $statusChecks.PSObject.Properties['checks']
  $contextsProperty = $statusChecks.PSObject.Properties['contexts']
  if ($null -eq $checksProperty -or $checksProperty.Value -isnot [array] -or
      $null -eq $contextsProperty -or $contextsProperty.Value -isnot [array]) {
    throw "Required-status-check bindings are incomplete; stop before mutation."
  }

  $bindings = [Collections.Generic.Dictionary[string, int64]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($check in @($statusChecks.checks)) {
    if ($null -eq $check -or $check -isnot [pscustomobject] -or
        $null -eq $check.PSObject.Properties['context'] -or
        $null -eq $check.PSObject.Properties['app_id'] -or
        $check.context -isnot [string] -or
        [string]::IsNullOrWhiteSpace($check.context)) {
      throw "Required-status-check state contains an invalid context."
    }
    $appId = ConvertTo-BranchProtectionAppId $check.app_id
    if ($bindings.ContainsKey([string]$check.context) -and
        $bindings[[string]$check.context] -ne $appId) {
      throw "Required-status-check state contains conflicting App bindings."
    }
    $bindings[[string]$check.context] = $appId
  }
  foreach ($context in @($statusChecks.contexts)) {
    if ($context -isnot [string] -or [string]::IsNullOrWhiteSpace($context)) {
      throw "Required-status-check state contains an invalid context."
    }
    if (-not $bindings.ContainsKey($context)) { $bindings[$context] = -1L }
  }
  $normalizedChecks = @(
    $bindings.Keys | Sort-Object -CaseSensitive | ForEach-Object {
      [ordered]@{ context = $_.ToLowerInvariant(); app_id = [int64]$bindings[$_] }
    }
  )
  return ([ordered]@{
    strict = $strictSnapshot
    checks = $normalizedChecks
  } | ConvertTo-Json -Depth 5 -Compress)
}

$protectionOutput = gh api --hostname github.com $protectionPath -H "Accept: application/vnd.github+json" 2>&1
$protectionExitCode = $LASTEXITCODE

if ($protectionExitCode -eq 0) {
  $existingProtection = $protectionOutput | ConvertFrom-Json
  $initialStatusCheckState = Get-RequiredStatusCheckSnapshot $existingProtection
  $initialReviewProtection = Get-BranchProtectionReviewState $existingProtection
  $checksByContext = [System.Collections.Specialized.OrderedDictionary]::new(
    [System.StringComparer]::OrdinalIgnoreCase
  )
  $bindingProblems = @()

  # Preserve every existing required check, including any GitHub App binding.
  foreach ($check in @($existingProtection.required_status_checks.checks)) {
    if ($null -eq $check -or [string]::IsNullOrWhiteSpace($check.context)) { continue }
    $entry = @{ context = $check.context }
    # A response value of null means the check accepts any app. In update requests,
    # GitHub requires -1 to preserve that behavior; omitting app_id may auto-select
    # the app that most recently supplied the check.
    try {
      $entry.app_id = ConvertTo-BranchProtectionAppId $check.app_id
    } catch {
      $bindingProblems += "$($check.context)=invalid app binding"
      continue
    }
    if ($checksByContext.Contains($check.context) -and
        [int64]$checksByContext[$check.context].app_id -ne [int64]$entry.app_id) {
      $bindingProblems += "$($check.context)=multiple existing app bindings"
    } else {
      $checksByContext[$check.context] = $entry
    }
  }
  foreach ($context in @($existingProtection.required_status_checks.contexts)) {
    if (-not [string]::IsNullOrWhiteSpace($context) -and -not $checksByContext.Contains($context)) {
      $checksByContext[$context] = @{ context = $context; app_id = -1 }
    }
  }
  foreach ($context in $requiredCheckNames) {
    $expectedAppId = $requiredAppIdsByContext[$context]
    if ($checksByContext.Contains($context)) {
      if ([int64]$checksByContext[$context].app_id -ne $expectedAppId) {
        $bindingProblems += "${context}=existing binding does not match verified app $expectedAppId"
      }
    } else {
      $checksByContext[$context] = @{ context = $context; app_id = $expectedAppId }
    }
  }
  if ($bindingProblems.Count -gt 0) {
    throw "Existing required-check bindings conflict with verified producers; no protection was changed: $($bindingProblems -join ', ')."
  }

  $requiredCheckPreflight = Assert-FreshRequiredCheckPreflight
  $statusProtectionBeforeWriteOutput = gh api --hostname github.com $protectionPath `
    -H "Accept: application/vnd.github+json" 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Could not re-read branch protection immediately before updating required checks; no new write was sent. $($statusProtectionBeforeWriteOutput | Out-String)"
  }
  try {
    $statusProtectionBeforeWrite = ($statusProtectionBeforeWriteOutput | Out-String) | ConvertFrom-Json
  } catch {
    throw "Branch-protection re-read returned invalid JSON; no new write was sent."
  }
  if ((Get-RequiredStatusCheckSnapshot $statusProtectionBeforeWrite) -cne
      $initialStatusCheckState) {
    throw "Required status checks changed after the initial read; do not overwrite the concurrent policy. Rerun the full protection review."
  }
  $statusPayload = @{
    strict = $true
    checks = @($checksByContext.Values)
  } | ConvertTo-Json -Depth 6
  $completedProtectionUpdates = @()
  $protectionUpdateFailure = $null
  $expectedReviewApprovalCount = $null
  try {
    $statusUpdateOutput = $statusPayload | gh api --hostname github.com -X PATCH `
      "$protectionPath/required_status_checks" `
      -H "Accept: application/vnd.github+json" --input - 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to update required status checks. $($statusUpdateOutput | Out-String)"
    }
    $completedProtectionUpdates += "required_status_checks"

    $null = Assert-FreshRequiredCheckPreflight
    $adminStateOutput = gh api --hostname github.com `
      "$protectionPath/enforce_admins" -H "Accept: application/vnd.github+json" 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "Could not re-read admin enforcement before its write. $($adminStateOutput | Out-String)"
    }
    try { $adminState = ($adminStateOutput | Out-String) | ConvertFrom-Json } catch {
      throw "Admin-enforcement re-read returned invalid JSON."
    }
    if ($adminState.enabled -isnot [bool]) {
      throw "Admin-enforcement re-read is incomplete; do not mutate."
    }
    if (-not $adminState.enabled) {
      $adminUpdateOutput = gh api --hostname github.com -X POST `
        "$protectionPath/enforce_admins" -H "Accept: application/vnd.github+json" `
        --silent 2>&1
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to enable admin enforcement. $($adminUpdateOutput | Out-String)"
      }
      $completedProtectionUpdates += "enforce_admins"
    }

    # Enable the PR requirement only when it is absent. Never weaken an existing review policy.
    if ($null -eq $initialReviewProtection) {
      $null = Assert-FreshRequiredCheckPreflight
      $reviewProtectionBeforeWriteOutput = gh api --hostname github.com $protectionPath `
        -H "Accept: application/vnd.github+json" 2>&1
      if ($LASTEXITCODE -ne 0) {
        throw "Could not re-read pull request review protection before its write. $($reviewProtectionBeforeWriteOutput | Out-String)"
      }
      try {
        $reviewProtectionBeforeWrite = ($reviewProtectionBeforeWriteOutput | Out-String) | ConvertFrom-Json
      } catch {
        throw "Branch-protection re-read returned invalid JSON; do not mutate review protection."
      }
      $reviewProtectionBeforeWriteState =
        Get-BranchProtectionReviewState $reviewProtectionBeforeWrite
      if ($null -eq $reviewProtectionBeforeWriteState) {
        $reviewUpdateOutput = '{"required_approving_review_count":0}' | `
          gh api --hostname github.com -X PATCH `
            "$protectionPath/required_pull_request_reviews" `
            -H "Accept: application/vnd.github+json" --input - 2>&1
        if ($LASTEXITCODE -ne 0) {
          throw "Failed to enable pull request review protection. $($reviewUpdateOutput | Out-String)"
        }
        $expectedReviewApprovalCount = 0
        $completedProtectionUpdates += "required_pull_request_reviews"
      } else {
        Write-Output "A concurrent review policy appeared; preserve it without an update."
      }
    }
  } catch {
    $protectionUpdateFailure = $_.Exception.Message
  }

  # These REST subresources cannot be updated atomically. Always re-read the final
  # state so a later failure cannot be mistaken for an all-or-nothing result.
  $finalProtectionOutput = gh api --hostname github.com $protectionPath `
    -H "Accept: application/vnd.github+json" 2>&1
  $finalProtectionExitCode = $LASTEXITCODE
  if ($finalProtectionExitCode -ne 0) {
    if ($null -ne $protectionUpdateFailure) {
      throw "$protectionUpdateFailure The update sequence is non-atomic and the final protection state could not be read; it may be partially updated. Completed calls: $($completedProtectionUpdates -join ', ')."
    }
    throw "Branch protection was updated, but its final state could not be verified. $($finalProtectionOutput | Out-String)"
  }
  $finalProtection = ($finalProtectionOutput | Out-String) | ConvertFrom-Json
  $finalProtectionSummary = [ordered]@{
    checks = @($finalProtection.required_status_checks.checks | ForEach-Object {
      [ordered]@{ context = $_.context; app_id = $_.app_id }
    })
    admins = [bool]$finalProtection.enforce_admins.enabled
    pull_request_reviews = $null -ne $finalProtection.required_pull_request_reviews
  } | ConvertTo-Json -Depth 6 -Compress
  if ($null -ne $protectionUpdateFailure) {
    if ($completedProtectionUpdates.Count -gt 0) {
      throw "$protectionUpdateFailure The non-atomic sequence partially updated protection. Confirmed successful calls: $($completedProtectionUpdates -join ', '). Final state: $finalProtectionSummary"
    }
    throw "$protectionUpdateFailure No update call was confirmed successful. Final state: $finalProtectionSummary"
  }
  $expectedFinalAppIds = [Collections.Generic.Dictionary[string, int64]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($context in @($checksByContext.Keys)) {
    $expectedFinalAppIds[$context] = [int64]$checksByContext[$context].app_id
  }
  Assert-ClassicProtectionState $finalProtection $expectedFinalAppIds $expectedReviewApprovalCount
} elseif (($protectionOutput | Out-String) -match '(?is)Branch not protected.*HTTP 404') {
  # No protection exists, so a complete initial payload cannot overwrite prior policy.
  $checks = @($requiredCheckNames | ForEach-Object {
    @{ context = $_; app_id = $requiredAppIdsByContext[$_] }
  })
  $payload = @{
    required_status_checks = @{
      strict = $true
      # GitHub's request schema treats `contexts` and `checks` as alternative
      # shapes. Send only `checks` so each context keeps its verified app binding.
      checks = $checks
    }
    enforce_admins = $true
    required_pull_request_reviews = @{ required_approving_review_count = 0 }
    restrictions = $null
  } | ConvertTo-Json -Depth 6

  $requiredCheckPreflight = Assert-FreshRequiredCheckPreflight
  $protectionBeforeCreateOutput = gh api --hostname github.com $protectionPath `
    -H "Accept: application/vnd.github+json" 2>&1
  if ($LASTEXITCODE -eq 0) {
    throw "Branch protection appeared after the initial read; do not replace it. Rerun the full protection review."
  }
  if (($protectionBeforeCreateOutput | Out-String) -notmatch '(?is)Branch not protected.*HTTP 404') {
    throw "Could not verify that branch protection is still absent; do not create it. $($protectionBeforeCreateOutput | Out-String)"
  }
  $createOutput = $payload | gh api --hostname github.com -X PUT $protectionPath `
    -H "Accept: application/vnd.github+json" --input - 2>&1
  $createExitCode = $LASTEXITCODE
  if ($createExitCode -ne 0) {
    $createError = $createOutput | Out-String
    if ($createError -match '(?is)HTTP 403') {
      throw "GitHub forbade branch-protection creation. Verify repository plan and Administration permission; no protection was created. $createError"
    }
    if ($createError -match '(?is)HTTP 404') {
      throw "The repository or detected default branch was not found or is inaccessible; no protection was created. Re-verify repository identity and DEFAULT_BRANCH. $createError"
    }
    if ($createError -match '(?is)HTTP 422') {
      throw "GitHub rejected the branch-protection request as invalid or abuse-limited; no protection was created. Inspect the preserved response and correct the payload or retry policy. $createError"
    }
    throw "Failed to create branch protection. $createError"
  }
  $createdProtectionOutput = gh api --hostname github.com $protectionPath `
    -H "Accept: application/vnd.github+json" 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Branch protection creation returned success, but its final state could not be verified. $($createdProtectionOutput | Out-String)"
  }
  $createdProtection = ($createdProtectionOutput | Out-String) | ConvertFrom-Json
  Assert-ClassicProtectionState $createdProtection $requiredAppIdsByContext 0
} elseif (($protectionOutput | Out-String) -match '(?is)HTTP 403') {
  throw "GitHub forbade branch-protection inspection. Verify repository plan and Administration permission; no settings were changed. $($protectionOutput | Out-String)"
} elseif (($protectionOutput | Out-String) -match '(?is)HTTP 404') {
  throw "The repository or detected default branch was not found or is inaccessible. Re-verify repository identity and DEFAULT_BRANCH; no settings were changed. $($protectionOutput | Out-String)"
} else {
  throw "Could not read existing branch protection; no settings were changed. $($protectionOutput | Out-String)"
}
```

`ci-success là aggregate test gate trong assets/workflows/ci.yml: chỉ xanh khi
mọi test matrix job đạt. Bắt buộc aggregate này thay vì từng matrix combination
giúp danh sách không phụ thuộc matrix. Dependency review và Conventional Commit
gate độc lập commitlint là job riêng, nên bắt buộc riêng nếu repo-scaffold asset
tương ứng đã cài. Cả ba required-check workflow đi kèm đều có coverage
pull_request không filter và merge_group. Workflow có sẵn có thể dùng job ID/name,
trigger/filter khác; giữ nguyên file, chỉ bắt buộc context xác minh từ định nghĩa
thật và event coverage.

Protected-branch API GitHub có write riêng cho các setting và không tài liệu hóa
conditional write. Trước mỗi write, ví dụ chạy lại required-check evidence và
đọc lại state bị ảnh hưởng. Nó so binding required-check với snapshot đầu trước
khi thay, giữ review protection xuất hiện đồng thời và xác nhận protection vẫn
chưa có trước khi tạo. State đổi thì dừng chuỗi; GET cuối báo partial update.
Sau mỗi lần đọc cuối vẫn còn race cỡ một request; phối hợp một writer duy nhất
hoặc không tự động hóa nếu race còn lại không chấp nhận được. Không gọi chuỗi
này là atomic.

## Tương thích ruleset (chỉ kiểm tra)

Ruleset có thể cùng tồn tại với classic branch protection; rule áp dụng nghiêm
ngặt nhất có hiệu lực. Plugin chỉ cấu hình classic branch protection vì tạo/sửa
ruleset an toàn cần giữ policy repository/organization, bypass actor, target
condition và tham số riêng từng rule. Không gọi POST/PUT/DELETE
repos/OWNER/REPO/rulesets trong scaffold và không tuyên bố đã cấu hình ruleset.

Trước khi đổi classic protection hoặc cài auto-merge, kiểm tra effective rule
trên default branch đã phát hiện qua repos/OWNER/REPO/rules/branches/BRANCH. Báo
ruleset repository/tổ chức là policy sẵn có, giữ nguyên và dừng nếu rule hiệu
lực xung đột classic setting đề xuất. Trong ruleset required_status_checks,
field bind source là integration_id, không phải classic protection app_id. Nếu
người dùng muốn tạo/sửa ruleset, coi đây là task thiết kế policy riêng cần phê
duyệt tường minh và payload đầy đủ đã review.

## Label

GitHub tạo các label mặc định này cho repository mới, nhưng chúng có thể bị sửa
hoặc xóa. Tạo lại nếu thiếu:

```powershell
$repositorySettingsPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/repository_settings_preflight.py"
if (-not (Test-Path -LiteralPath $repositorySettingsPreflight -PathType Leaf)) {
  throw "The bundled repository-settings preflight is missing; do not create labels."
}
# Populate this list only from the exact optional assets selected in the
# approved scaffold plan. The allowlist and labels come from the checked-in assets.
$selectedOptionalLabelAssets = @() # e.g. "labeler.yml" only when that asset is selected
$optionalLabelNamesByAsset = [ordered]@{
  "auto-merge.yml" = @("automerge")
  "labeler.yml" = @("ci", "tests")
  "release-config.yml" = @("feature", "fix", "ignore-for-release")
  "stale.yml" = @("Stale", "pinned", "security")
}
$allowedOptionalLabelAssets = @(
  "auto-merge.yml", "labeler.yml", "release-config.yml", "stale.yml"
)
$selectedOptionalLabelAssetSet = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::Ordinal
)
foreach ($assetName in @($selectedOptionalLabelAssets)) {
  if ($assetName -isnot [string] -or
      $allowedOptionalLabelAssets -cnotcontains $assetName -or
      -not $selectedOptionalLabelAssetSet.Add($assetName)) {
    throw "Label plan contains an unsupported or duplicate optional asset: '$assetName'."
  }
}
$plannedLabelNames = @(
  "bug", "documentation", "duplicate", "enhancement", "good first issue",
  "help wanted", "invalid", "question", "wontfix"
)
foreach ($assetName in $selectedOptionalLabelAssets) {
  $plannedLabelNames += @($optionalLabelNamesByAsset[[string]$assetName])
}
$plannedLabelNameSet = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($labelName in $plannedLabelNames) {
  if (-not $plannedLabelNameSet.Add([string]$labelName)) {
    throw "Label plan contains a duplicate label: '$labelName'."
  }
}
$labelPreflightArguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID)
foreach ($labelName in $plannedLabelNames) { $labelPreflightArguments += @("--create-label", $labelName) }
$labelPreflightOutput = python $repositorySettingsPreflight @labelPreflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Label inspection is inconclusive; do not create labels. $($labelPreflightOutput | Out-String)"
}
try {
  $labelPreflightText = $labelPreflightOutput | Out-String
  if (-not $labelPreflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
  $labelPreflight = $labelPreflightText | ConvertFrom-Json -ErrorAction Stop
} catch {
  throw "Repository-settings preflight returned invalid JSON; do not create labels."
}
Assert-SelectedRepositoryId -RepositoryId $labelPreflight.repository_id
if ($labelPreflight -isnot [pscustomobject] -or
    $labelPreflight.repository -isnot [string] -or $labelPreflight.decision -isnot [string] -or
    $labelPreflight.inspection_complete -isnot [bool] -or
    -not $labelPreflight.inspection_complete -or
    $labelPreflight.decision -ne "may-configure-repository-settings" -or
    @($labelPreflight.requested_mutations) -notcontains "labels") {
  throw "Repository-settings preflight did not approve the planned label mutations."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  [string]$labelPreflight.repository, "OWNER/REPO"
)) {
  throw "Repository-settings preflight returned a different repository; do not create labels."
}
$approvedLabelSettings = $labelPreflight.requested_settings
if ($approvedLabelSettings -isnot [pscustomobject] -or
    $approvedLabelSettings.labels -isnot [Array] -or
    @($approvedLabelSettings.labels | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
    $labelPreflight.requested_mutations -isnot [Array] -or
    $labelPreflight.requested_mutations.Count -ne 1 -or
    $labelPreflight.requested_mutations[0] -isnot [string] -or
    $labelPreflight.requested_mutations[0] -cne "labels") {
  throw "Repository-settings preflight did not return the approved label input."
}
$approvedLabels = @($approvedLabelSettings.labels)
if ($approvedLabels.Count -ne $plannedLabelNames.Count -or
    $null -ne (Compare-Object -ReferenceObject $plannedLabelNames -DifferenceObject $approvedLabels -CaseSensitive)) {
  throw "Repository-settings preflight input does not match the planned label mutations."
}

$existingLabels = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
$labelListOutput = gh api --hostname github.com --paginate `
  "repos/OWNER/REPO/labels?per_page=100" --jq '.[].name'
$labelListExitCode = $LASTEXITCODE
if ($labelListExitCode -ne 0) { throw "Failed to list existing labels; no labels were changed." }
foreach ($labelName in @($labelListOutput)) {
  [void]$existingLabels.Add([string]$labelName)
}

function Assert-CurrentLabelRepository {
  $output = gh api --hostname github.com repos/OWNER/REPO 2>&1
  if ($LASTEXITCODE -ne 0) { throw "Label target identity could not be verified; state remains unverified." }
  $text = $output | Out-String
  if (-not $text.TrimStart().StartsWith("{")) { throw "Label target response must be an object." }
  $target = $text | ConvertFrom-Json -ErrorAction Stop
  Assert-SelectedRepositoryId -RepositoryId $target.id
  if ($target -isnot [pscustomobject] -or $target.full_name -isnot [string] -or
      $target.archived -isnot [bool] -or $target.disabled -isnot [bool] -or
      $target.archived -or $target.disabled -or
      -not [StringComparer]::OrdinalIgnoreCase.Equals($target.full_name, "OWNER/REPO")) {
    throw "Label readback belongs to an unverified repository; stop configuration."
  }
}

function Get-BoundActiveLabel {
  param([Parameter(Mandatory)][string]$Name)

  Assert-CurrentLabelRepository
  $label = $null
  try {
    $encodedName = [Uri]::EscapeDataString($Name)
    $output = gh api --hostname github.com "repos/OWNER/REPO/labels/$encodedName" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Label '$Name' could not be observed; state remains unverified." }
    $text = $output | Out-String
    if (-not $text.TrimStart().StartsWith("{")) { throw "Label readback must be an object." }
    $label = $text | ConvertFrom-Json -ErrorAction Stop
    if ($label -isnot [pscustomobject] -or
        ($label.id -isnot [int] -and $label.id -isnot [long]) -or $label.id -le 0 -or
        $label.name -isnot [string] -or $label.color -isnot [string] -or
        $label.color -notmatch '^[0-9a-fA-F]{6}$' -or
        $null -eq $label.PSObject.Properties['description'] -or
        ($null -ne $label.description -and $label.description -isnot [string]) -or
        $null -eq $label.PSObject.Properties['archived_at'] -or
        $null -eq $label.PSObject.Properties['archived_by'] -or
        $null -ne $label.archived_at -or $null -ne $label.archived_by -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals($label.name, $Name)) {
      throw "Label '$Name' has a malformed, mismatched or archived receipt; do not use it as an active prerequisite."
    }
  } finally {
    Assert-CurrentLabelRepository
  }
  return $label
}

function Add-LabelIfMissing {
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Color,
    [Parameter(Mandatory)][string]$Description
  )

  if ($existingLabels.Contains($Name)) {
    $null = Get-BoundActiveLabel -Name $Name
    return
  }
  $null = Get-ValidatedLabelPreflight -Name $Name
  $createOutput = gh label create $Name --repo github.com/OWNER/REPO `
    --color $Color --description $Description 2>&1
  $createExitCode = $LASTEXITCODE

  # Re-read the label even after a failed create. Another actor may have created it
  # after the initial list, and a successful response still needs state verification.
  $label = Get-BoundActiveLabel -Name $Name
  $labelMatches = [StringComparer]::OrdinalIgnoreCase.Equals(
    $label.name,
    $Name
  ) -and [StringComparer]::OrdinalIgnoreCase.Equals(
    $label.color,
    $Color.TrimStart("#")
  ) -and $label.description -ceq $Description
  if (-not $labelMatches) {
    throw "Label '$Name' exists after the create attempt but does not match the requested color and description. It was not overwritten."
  }
  if ($createExitCode -ne 0) {
    Write-Warning "The label create call failed, but a concurrent matching label now exists. $($createOutput | Out-String)"
  }
  [void]$existingLabels.Add($Name)
}

function Get-ValidatedLabelPreflight {
  param([Parameter(Mandatory)][string]$Name)

  $output = python $repositorySettingsPreflight `
    --repository "OWNER/REPO" --hostname "github.com" `
    --expected-repository-id $SELECTED_REPOSITORY_ID --create-label $Name 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    throw "Fresh label preflight is inconclusive for '$Name'; do not create it. $($output | Out-String)"
  }
  try {
    $preflightText = $output | Out-String
    if (-not $preflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
    $result = $preflightText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Fresh label preflight returned invalid JSON for '$Name'; do not create it."
  }
  if ($result -isnot [pscustomobject] -or
      $result.repository -isnot [string] -or $result.decision -isnot [string] -or
      $result.requested_mutations -isnot [Array] -or
      @($result.requested_mutations | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
      $result.requested_settings -isnot [pscustomobject] -or
      $result.requested_settings.labels -isnot [Array] -or
      @($result.requested_settings.labels | Where-Object { $_ -isnot [string] }).Count -gt 0) {
    throw "Fresh label preflight has an invalid schema; do not create the label."
  }
  $mutations = @($result.requested_mutations)
  $labels = @($result.requested_settings.labels)
  Assert-SelectedRepositoryId -RepositoryId $result.repository_id
  if ($result.inspection_complete -isnot [bool] -or
      -not $result.inspection_complete -or
      $result.decision -ne "may-configure-repository-settings" -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$result.repository, "OWNER/REPO"
      ) -or $mutations.Count -ne 1 -or $mutations[0] -cne "labels" -or
      $labels.Count -ne 1 -or $labels[0] -cne $Name) {
    throw "Fresh label preflight did not approve exactly '$Name' for OWNER/REPO; do not create it."
  }
  return $result
}

Add-LabelIfMissing "bug"              d73a4a "Something is not working"
Add-LabelIfMissing "documentation"    0075ca "Improvements or additions to documentation"
Add-LabelIfMissing "duplicate"        cfd3d7 "Similar issue, pull request, or discussion already exists"
Add-LabelIfMissing "enhancement"      a2eeef "New feature or request"
Add-LabelIfMissing "good first issue" 7057ff "Good for newcomers"
Add-LabelIfMissing "help wanted"      008672 "Extra attention is needed"
Add-LabelIfMissing "invalid"          e4e669 "This is no longer relevant"
Add-LabelIfMissing "question"         d876e3 "Further information is requested"
Add-LabelIfMissing "wontfix"          ffffff "This will not be worked on"
```

Chỉ tạo label cho workflow/config tùy chọn nếu asset tương ứng được chọn trong
`$selectedOptionalLabelAssets`. Labeler đi kèm có `pull-requests: write` nhưng
không có `issues: write`, vì vậy mọi label trong `.github/labeler.yml` phải có
sẵn khi workflow chạy. Với từng asset đã chọn, chạy đúng workflow-installation
preflight rồi gọi `Assert-CurrentPlannedLabels` ngay trước khi copy. Lặp lại cả
hai check trước mỗi lần copy asset riêng; không dùng lại label đã đọc từ
preflight/copy trước.

Workflow phụ thuộc không thể gán archived label. Giữ state hiện có, dừng plan bị
ảnh hưởng và hỏi cách remediation rõ ràng thay vì tự tạo, ghi đè hoặc unarchive.
Mỗi active-label receipt có type được xác minh và được kẹp bằng numeric
repository identity ban đầu; đây là bằng chứng state hiện tại, không đảm bảo REST
write theo tên là atomic.

```powershell
if ($selectedOptionalLabelAssets -contains "auto-merge.yml") {
  Add-LabelIfMissing "automerge" 0e8a16 "Auto-merge when CI is green"
}

if ($selectedOptionalLabelAssets -contains "labeler.yml") {
  Add-LabelIfMissing "ci"    5319e7 "Changes to CI configuration"
  Add-LabelIfMissing "tests" bfdadc "Changes to tests"
}

if ($selectedOptionalLabelAssets -contains "release-config.yml") {
  Add-LabelIfMissing "feature"            a2eeef "Feature work for release notes"
  Add-LabelIfMissing "fix"                d73a4a "Bug fix for release notes"
  Add-LabelIfMissing "ignore-for-release" ffffff "Exclude from generated release notes"
}

if ($selectedOptionalLabelAssets -contains "stale.yml") {
  Add-LabelIfMissing "Stale"    ededed "Inactive issue or pull request"
  Add-LabelIfMissing "pinned"   1d76db "Exempt from stale automation"
  Add-LabelIfMissing "security" b60205 "Security-related work or report"
}

$finalLabelListOutput = gh api --hostname github.com --paginate `
  "repos/OWNER/REPO/labels?per_page=100" --jq '.[].name'
if ($LASTEXITCODE -ne 0) {
  throw "Could not verify the final label state after configuration."
}
$finalLabelNames = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($labelName in @($finalLabelListOutput)) {
  [void]$finalLabelNames.Add([string]$labelName)
}
$missingFinalLabels = @($plannedLabelNames | Where-Object {
  -not $finalLabelNames.Contains([string]$_)
})
if ($missingFinalLabels.Count -gt 0) {
  throw "Some planned labels are missing from the final repository state: $($missingFinalLabels -join ', ')."
}

function Assert-CurrentPlannedLabels {
  foreach ($labelName in $plannedLabelNames) {
    $null = Get-BoundActiveLabel -Name $labelName
  }
}
Assert-CurrentPlannedLabels
```

## Dependabot

Giữ root `pip` block cố định trong asset vì mọi scaffold đều cài
`requirements-docs.txt`, kể cả project không có application manifest. Giữ group
`github-actions` cố định cho version/security update với `patterns: ["*"]` để
mọi action dependency, gồm nhiều sub-action từ một repository, cập nhật trong
một PR cho mỗi update class.

Chỉ render block bổ sung từ manifest thật sự có trong first-party candidate list
đã lọc. Loại path có thành phần `node_modules`, `.venv`, `venv`, `vendor`, `dist`,
`build` hoặc `target` kể cả khi tracked, trừ khi user xác nhận rõ path đó là
first-party workspace. Với mỗi application ecosystem được hỗ trợ, dùng đúng
`package-ecosystem` identifier GitHub và repository-relative directory chứa
manifest. Không tạo root `pip` block trùng cho Python manifest ở root; block cố
định đã bao phủ mọi root requirements file được hỗ trợ. Dùng `directories` cho
nhiều location bổ sung không chồng lấn của cùng ecosystem, hoặc tách block khi
setting khác nhau.

Mỗi block tạo ra phải có `commit-message.prefix: "chore(deps)"`; Dependabot áp
prefix cho cả commit message và PR title, nhờ vậy qua `commitlint.yml` khi
workflow tùy chọn đó được cài. Giữ `github-actions` block cố định của asset với
`directory: "/"` để workflow action đã pin tiếp tục được cập nhật.

Ví dụ replacement cho toàn bộ marker line
`  # {{REPO_SCAFFOLD_DEPENDABOT_PACKAGE_UPDATES}}` khi có npm manifest ở root và
trong `packages/web`:

```yaml
  - package-ecosystem: "npm"
    directories:
      - "/"
      - "/packages/web"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "chore(deps)"
```

Nếu không có application location được hỗ trợ bổ sung, xóa marker line và giữ
root `pip` cùng `github-actions` block cố định. Nếu có, replacement phải kết
thúc bằng newline để list item `github-actions` phía sau vẫn hợp lệ YAML.
Validate asset và file đã render; chỉ yêu cầu token chính xác
`{{REPO_SCAFFOLD_DEPENDABOT_PACKAGE_UPDATES}}` đã biến mất. Không dùng regex
double-brace chung: Helm/Jinja/Angular expression của project không phải
repo-scaffold placeholder, còn `${{ ... }}` là GitHub Actions syntax hợp lệ.
Khi commit thành `.github/dependabot.yml`, GitHub tự nhận cấu hình, không cần
API call.

## Tính năng bảo mật

Kiểm tra capability repository trước khi đổi setting:

```bash
gh repo view github.com/OWNER/REPO --json visibility,isFork,isArchived,hasIssuesEnabled,hasDiscussionsEnabled,owner,defaultBranchRef
gh api --hostname github.com users/OWNER --jq '.type'
gh api --hostname github.com repos/OWNER/REPO --jq '.security_and_analysis'
```

Repository view trả owner login nhưng không trả account type. Coi kết quả Users
API riêng là `OWNER_TYPE`, chỉ nhận `User` hoặc `Organization`; nếu lookup lỗi
hoặc trả giá trị khác thì hỏi, không đoán. Dùng giá trị này để chọn CODEOWNERS
và xét eligibility dependency-review cho private repo.

Sau khi xác nhận, chạy riêng từng lệnh đủ điều kiện; chỉ báo feature đã bật sau
khi xác minh state cuối. Coi `403` là forbidden và `404` là thiếu/không truy cập
được, trừ khi evidence theo endpoint chứng minh giới hạn capability. Giữ và xem
từng response `422`: tùy endpoint, nó có thể là input sai, không đủ điều kiện
hoặc abuse control; không tự gán lại thành lỗi plan/permission. Coi `409` là
operation đang chạy/xung đột, `503` là service tạm thời không khả dụng. Báo
capability/validation/retry result đã xác minh rồi tiếp tục, không tuyên bố thành
công hoặc fail toàn scaffold.

Trước security-feature mutation đầu tiên, chạy bundled
`scripts/security_features_preflight.py` với đúng feature flag đã duyệt. Script
chỉ đọc, fail-closed: bind response với `OWNER/REPO` tường minh, từ chối
repository archived/disabled/nhập nhằng, xác minh field security-analysis công
bố và yêu cầu quyền admin hiện tại. Push protection cần secret scanning trước;
private vulnerability reporting chỉ cho repository công khai, kể cả fork.
Dependabot alerts phải được kiểm tra trước automated security fix trừ khi đã
yêu cầu bật alerts trước đó. Script không suy Secret Protection entitlement từ
field repository bị thiếu.

Với target private/internal, xác minh riêng
[eligibility GitHub Secret Protection](https://docs.github.com/en/code-security/concepts/secret-security/secret-scanning).
Repository-level [push protection cần Secret Protection](https://docs.github.com/en/code-security/concepts/secret-security/push-protection).
Trên GitHub.com, private repository do user sở hữu cần eligibility Enterprise
Managed Users đã tài liệu hóa. Chỉ truyền
`--confirm-private-secret-protection-eligibility` sau khi kiểm tra; nếu không,
preflight yêu cầu confirmation và cấm mutation. Flag ghi nhận giả định plan do
user xác minh, không query trạng thái billing. Tiếp tục xử lý riêng kết quả cuối
của GitHub `403`, `404`, `409`, `422`, `503`.

```powershell
$securityFeaturesPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/security_features_preflight.py"
if (-not (Test-Path -LiteralPath $securityFeaturesPreflight -PathType Leaf)) {
  throw "The bundled security-features preflight is missing; do not mutate security settings."
}

# Set each value only from explicit user approval.
$enableDependabotAlertsRequested = $false
$enableAutomatedSecurityFixesRequested = $false
$enableSecretScanningRequested = $false
$enablePushProtectionRequested = $false
$enablePrivateVulnerabilityReportingRequested = $false
# Set only after verifying Secret Protection eligibility for a private/internal target.
$confirmPrivateSecretProtectionEligibility = $false
foreach ($choice in @(
    "enableDependabotAlertsRequested", "enableAutomatedSecurityFixesRequested",
    "enableSecretScanningRequested", "enablePushProtectionRequested",
    "enablePrivateVulnerabilityReportingRequested", "confirmPrivateSecretProtectionEligibility")) {
  if ((Get-Variable -Name $choice -ValueOnly) -isnot [bool]) {
    throw "Security choices must be explicit Boolean values; do not infer approval."
  }
}
$securityPreflightArguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID)
if ($enableDependabotAlertsRequested) { $securityPreflightArguments += "--enable-dependabot-alerts" }
if ($enableAutomatedSecurityFixesRequested) { $securityPreflightArguments += "--enable-automated-security-fixes" }
if ($enableSecretScanningRequested) { $securityPreflightArguments += "--enable-secret-scanning" }
if ($enablePushProtectionRequested) { $securityPreflightArguments += "--enable-push-protection" }
if ($enablePrivateVulnerabilityReportingRequested) { $securityPreflightArguments += "--enable-private-vulnerability-reporting" }
if ($confirmPrivateSecretProtectionEligibility -and
    ($enableSecretScanningRequested -or $enablePushProtectionRequested)) {
  $securityPreflightArguments += "--confirm-private-secret-protection-eligibility"
}

$requestedSecurityFeatures = @()
if ($enableDependabotAlertsRequested) { $requestedSecurityFeatures += "dependabot_alerts" }
if ($enableAutomatedSecurityFixesRequested) { $requestedSecurityFeatures += "automated_security_fixes" }
if ($enableSecretScanningRequested) { $requestedSecurityFeatures += "secret_scanning" }
if ($enablePushProtectionRequested) { $requestedSecurityFeatures += "push_protection" }
if ($enablePrivateVulnerabilityReportingRequested) { $requestedSecurityFeatures += "private_vulnerability_reporting" }
if ($confirmPrivateSecretProtectionEligibility -and
    -not ($enableSecretScanningRequested -or $enablePushProtectionRequested)) {
  throw "Secret Protection eligibility confirmation requires a secret-scanning or push-protection request."
}
function Assert-SecurityFeaturePreflightSchema {
  param([object]$Verdict)
  if ($Verdict -isnot [pscustomobject]) { throw "Security verdict must be a JSON object." }
  Assert-SelectedRepositoryId -RepositoryId $Verdict.repository_id
  foreach ($field in @("inspection_complete", "administration_permission", "is_fork")) {
    if ($Verdict.$field -isnot [bool]) { throw "Security verdict has an invalid Boolean field." }
  }
  if (-not $Verdict.administration_permission) { throw "Security administration permission is unverified." }
  if ($Verdict.repository -isnot [string] -or
      -not [StringComparer]::OrdinalIgnoreCase.Equals($Verdict.repository, "OWNER/REPO") -or
      $Verdict.decision -isnot [string] -or
      $Verdict.visibility -isnot [string] -or $Verdict.visibility -cnotin @("public", "private", "internal") -or
      $Verdict.owner_type -isnot [string] -or $Verdict.owner_type -cnotin @("User", "Organization") -or
      $Verdict.private_security_feature_eligibility -isnot [string] -or
      $Verdict.private_security_feature_eligibility -cnotin @("not-required", "confirmation-required", "user-confirmed")) {
    throw "Security verdict has an invalid identity, applicability or decision field."
  }
  $knownFeatures = @("dependabot_alerts", "automated_security_fixes", "secret_scanning", "push_protection", "private_vulnerability_reporting")
  if ($Verdict.requested_features -isnot [array] -or $Verdict.requested_features.Count -lt 1 -or
      $Verdict.requested_features.Count -gt $knownFeatures.Count) { throw "Security feature plan is missing or out of bounds." }
  $features = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach ($feature in $Verdict.requested_features) {
    if ($feature -isnot [string] -or $feature -cnotin $knownFeatures -or -not $features.Add($feature)) {
      throw "Security feature plan contains a malformed or duplicate entry."
    }
  }
  if ($Verdict.security_and_analysis -isnot [pscustomobject]) { throw "Security analysis evidence must be an object." }
  foreach ($field in @("dependabot_security_updates", "secret_scanning", "secret_scanning_push_protection")) {
    if ($null -eq $Verdict.security_and_analysis.PSObject.Properties[$field] -or
        ($null -ne $Verdict.security_and_analysis.$field -and
         ($Verdict.security_and_analysis.$field -isnot [string] -or $Verdict.security_and_analysis.$field -cnotin @("enabled", "disabled")))) {
      throw "Security status evidence is missing or malformed."
    }
  }
  if ($null -eq $Verdict.PSObject.Properties['dependabot_alerts_precondition']) { throw "Dependabot prerequisite evidence is missing." }
  if ($features.Contains("private_vulnerability_reporting") -and $Verdict.visibility -cne "public") {
    throw "Private vulnerability reporting requires a public repository."
  }
  if ($features.Contains("push_protection") -and -not $features.Contains("secret_scanning") -and
      $Verdict.security_and_analysis.secret_scanning -cne "enabled") {
    throw "Push protection has no observed or approved prior secret-scanning prerequisite."
  }
  if ($features.Contains("automated_security_fixes")) {
    if ($Verdict.dependabot_alerts_precondition -isnot [string] -or
        $Verdict.dependabot_alerts_precondition -cnotin @("verified-enabled", "requested-for-prior-enable") -or
        ($Verdict.dependabot_alerts_precondition -ceq "requested-for-prior-enable" -and -not $features.Contains("dependabot_alerts"))) {
      throw "Automated fixes have no valid observed or prior-enable prerequisite."
    }
  } elseif ($null -ne $Verdict.dependabot_alerts_precondition) {
    throw "An unrelated feature plan cannot reuse automated-fix prerequisite evidence."
  }
}

$securityPreflightResult = $null
$approvedSecurityFeatures = @()
if ($requestedSecurityFeatures.Count -gt 0) {
  $securityPreflightOutput = python $securityFeaturesPreflight @securityPreflightArguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Security-feature inspection is inconclusive; do not mutate. $($securityPreflightOutput | Out-String)"
  }
  try {
    $securityVerdictText = $securityPreflightOutput | Out-String
    if ([Text.Encoding]::UTF8.GetByteCount($securityVerdictText) -gt 2097152) { throw "Security verdict exceeds the byte limit." }
    $securityPreflightResult = $securityVerdictText | ConvertFrom-Json
  } catch {
    throw "Security-feature preflight returned invalid JSON; do not mutate."
  }
  Assert-SecurityFeaturePreflightSchema -Verdict $securityPreflightResult
  if ($securityPreflightResult.decision -eq "confirm-private-secret-protection-eligibility") {
    throw "Verify GitHub Secret Protection eligibility for this private/internal repository, then set `$confirmPrivateSecretProtectionEligibility and rerun the preflight."
  }
  Assert-SelectedRepositoryId -RepositoryId $securityPreflightResult.repository_id
  if ($securityPreflightResult.inspection_complete -isnot [bool] -or
      -not $securityPreflightResult.inspection_complete -or
      $securityPreflightResult.decision -ne "may-configure-security-features") {
    throw "Security-feature preflight did not approve the requested mutations."
  }
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$securityPreflightResult.repository,
    "OWNER/REPO"
  )) {
    throw "Security-feature preflight returned a different repository; do not mutate."
  }
  $approvedSecurityFeatures = @($securityPreflightResult.requested_features | ForEach-Object { [string]$_ })
  if ($approvedSecurityFeatures.Count -ne $requestedSecurityFeatures.Count -or
      $null -ne (Compare-Object -ReferenceObject $requestedSecurityFeatures -DifferenceObject $approvedSecurityFeatures -CaseSensitive)) {
    throw "Security-feature preflight input does not match the requested mutations."
  }
  $privateSecretFeatureRequested =
    $securityPreflightResult.visibility -ne "public" -and
    @($requestedSecurityFeatures | Where-Object { $_ -in @("secret_scanning", "push_protection") }).Count -gt 0
  $expectedSecretProtectionEligibility = if ($privateSecretFeatureRequested) {
    "user-confirmed"
  } else {
    "not-required"
  }
  if ($securityPreflightResult.private_security_feature_eligibility -cne
      $expectedSecretProtectionEligibility) {
    throw "Secret Protection eligibility evidence does not match the requested features and repository visibility."
  }
} else {
  Write-Output "No security-feature changes were requested; skip the preflight and mutation blocks."
}
```

Danh sách feature được duyệt chỉ là phạm vi mutation dự kiến. Ngay trước write
của từng setting, chạy lại read-only preflight, bind repository và đúng một
feature, kiểm tra write exit code rồi đọc lại setting. Feature check fail hoặc
inconclusive chỉ skip feature đó cùng setting phụ thuộc; vẫn tiếp tục scaffold
không liên quan nhưng không tuyên bố feature đã bật.

Kết quả ban đầu và từng writer đều phải qua shared schema trước cast hoặc
applicability decision. Schema cần typed permission/target/owner/visibility/fork
field, bounded array feature đã biết không trùng và security/prerequisite
evidence nullable tường minh. JSON shape sai không cho phép mutation; null ở
field tùy chọn không chứng minh enablement. Giới hạn byte JSON trước parse và
phân biệt user approval với schema validation.

Với inspection nhiều request, producer xác minh lại Dependabot prerequisite,
rồi bind numeric identity và active/admin/visibility/owner/fork/security-analysis
state hiện tại với snapshot ban đầu. Drift làm verdict vô hiệu.
`verified-enabled` cần HTTP receipt chính xác `204 No Content`, không chỉ CLI
exit thành công hoặc response text tùy ý. `requested-for-prior-enable` vẫn có
nghĩa phải bật alerts rồi xác minh mới; nó không chứng minh alerts đã bật.

Đọc lại sau mọi write attempt kể cả acknowledgment lỗi: lỗi không chứng minh
remote state không đổi. Tách acknowledgment khỏi observed state, giữ response,
không rollback/replay tự động. Boolean/status malformed hoặc readback không đọc
được là unverified, không phải enablement. Bind repository-status readback với
target đã chọn trước khi nhận security-analysis field.

```powershell
function Get-ValidatedSecurityFeaturePreflight {
  param(
    [Parameter(Mandatory)][string[]]$FeatureArguments,
    [Parameter(Mandatory)][string]$ExpectedFeature,
    [switch]$ConfirmPrivateSecretProtectionEligibility
  )

  $arguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID) + @($FeatureArguments)
  if ($ConfirmPrivateSecretProtectionEligibility -and
      $ExpectedFeature -in @("secret_scanning", "push_protection")) {
    $arguments += "--confirm-private-secret-protection-eligibility"
  }
  $output = & python $securityFeaturesPreflight @arguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    Write-Warning "Security-feature preflight is inconclusive for $ExpectedFeature; skip this mutation. $($output | Out-String)"
    return $null
  }
  try {
    $securityVerdictText = $output | Out-String
    if ([Text.Encoding]::UTF8.GetByteCount($securityVerdictText) -gt 2097152) { throw "Security verdict exceeds the byte limit." }
    $result = $securityVerdictText | ConvertFrom-Json
  } catch {
    Write-Warning "Security-feature preflight returned invalid JSON for $ExpectedFeature; skip this mutation."
    return $null
  }
  Assert-SecurityFeaturePreflightSchema -Verdict $result
  if ($result.inspection_complete -isnot [bool] -or
      -not $result.inspection_complete -or
      $result.decision -ne "may-configure-security-features" -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$result.repository, "OWNER/REPO"
      )) {
    Write-Warning "Security-feature preflight did not approve $ExpectedFeature for OWNER/REPO; skip this mutation."
    return $null
  }
  $features = @($result.requested_features | ForEach-Object { [string]$_ })
  if ($features.Count -ne 1 -or $features[0] -cne $ExpectedFeature) {
    Write-Warning "Security-feature preflight input does not match $ExpectedFeature; skip this mutation."
    return $null
  }
  $requiresSecretProtectionConfirmation =
    $ExpectedFeature -in @("secret_scanning", "push_protection") -and
    $result.visibility -ne "public"
  if ($requiresSecretProtectionConfirmation) {
    if (-not $ConfirmPrivateSecretProtectionEligibility -or
        $result.private_security_feature_eligibility -cne "user-confirmed") {
      Write-Warning "Private Secret Protection eligibility is not confirmed for $ExpectedFeature; skip this mutation."
      return $null
    }
  } elseif ($result.private_security_feature_eligibility -cne "not-required") {
    Write-Warning "Security-feature preflight returned unexpected Secret Protection eligibility evidence; skip this mutation."
    return $null
  }
  return $result
}
```

- **Dependency graph:** bật mặc định cho repository công khai; đây không phải
  setting Dependabot alerts.
- **Dependabot alerts:** mặc định tắt. Bật tường minh khi được hỗ trợ. Automated
  security fix yêu cầu Dependabot alerts bật trước: nếu cả hai được duyệt trong
  một preflight, bật alerts, query lại `vulnerability-alerts` thành công rồi mới
  bật security fixes:

  ```powershell
  if ($enableDependabotAlertsRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-dependabot-alerts") `
      -ExpectedFeature "dependabot_alerts"
    if ($null -ne $featurePreflight) {
      $enableOutput = gh api --hostname github.com -X PUT `
        repos/OWNER/REPO/vulnerability-alerts 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Dependabot alerts write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com --include `
        repos/OWNER/REPO/vulnerability-alerts 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Dependabot alerts are not verified as enabled after the attempted write. $($verifyOutput | Out-String)"
      } else {
        $alertReceipt = $verifyOutput | Out-String
        if ([Text.Encoding]::UTF8.GetByteCount($alertReceipt) -gt 2097152 -or
            $alertReceipt -notmatch '\AHTTP/[0-9]+(?:\.[0-9]+)? 204[^\r\n]*\r?\n(?:[!#$%&''*+.^_`|~0-9A-Za-z-]+:[^\r\n]*\r?\n)*\r?\n[ \t\r\n]*\z') {
          Write-Warning "Dependabot alerts readback is not an exact no-content 204 receipt; do not claim enablement."
        }
      }
    }
  }
  if ($enableAutomatedSecurityFixesRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-automated-security-fixes") `
      -ExpectedFeature "automated_security_fixes"
    if ($null -ne $featurePreflight) {
      $enableOutput = gh api --hostname github.com -X PUT `
        repos/OWNER/REPO/automated-security-fixes 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Automated security fixes write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com `
        repos/OWNER/REPO/automated-security-fixes 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Automated security fixes state could not be verified after the attempted write. $($verifyOutput | Out-String)"
      } else {
        try { $fixState = ($verifyOutput | Out-String) | ConvertFrom-Json } catch {
          $fixState = $null
        }
        if ($null -eq $fixState -or
            $fixState.enabled -isnot [bool] -or $fixState.enabled -ne $true -or
            $fixState.paused -isnot [bool] -or $fixState.paused -ne $false) {
          Write-Warning "Automated security fixes are not verified as enabled and unpaused."
        }
      }
    }
  }
  ```

- **Secret scanning + push protection:** khả năng dùng phụ thuộc visibility của
  repository và entitlement bảo mật GitHub của owner. Push protection cần secret
  scanning. Chỉ thử sau capability check:

  ```powershell
  if ($enableSecretScanningRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-secret-scanning") `
      -ExpectedFeature "secret_scanning" `
      -ConfirmPrivateSecretProtectionEligibility:$confirmPrivateSecretProtectionEligibility
    if ($null -ne $featurePreflight) {
      $enableOutput = gh repo edit github.com/OWNER/REPO --enable-secret-scanning 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Secret scanning write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Secret-scanning state could not be read after the attempted write. $($verifyOutput | Out-String)"
      } else {
        try { $secretState = ($verifyOutput | Out-String) | ConvertFrom-Json } catch { $secretState = $null }
        if ($null -ne $secretState) { Assert-SelectedRepositoryId -RepositoryId $secretState.id }
        if ($null -eq $secretState -or $secretState.full_name -isnot [string] -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals($secretState.full_name, "OWNER/REPO") -or
            $secretState.security_and_analysis.secret_scanning.status -isnot [string] -or
            $secretState.security_and_analysis.secret_scanning.status -cne "enabled") {
          Write-Warning "Secret scanning is not verified as enabled for OWNER/REPO after the attempted write."
        }
      }
    }
  }
  if ($enablePushProtectionRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-push-protection") `
      -ExpectedFeature "push_protection" `
      -ConfirmPrivateSecretProtectionEligibility:$confirmPrivateSecretProtectionEligibility
    if ($null -ne $featurePreflight) {
      $enableOutput = gh repo edit github.com/OWNER/REPO `
        --enable-secret-scanning-push-protection 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Push protection write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Push-protection state could not be read after the attempted write. $($verifyOutput | Out-String)"
      } else {
        try { $pushProtectionState = ($verifyOutput | Out-String) | ConvertFrom-Json } catch { $pushProtectionState = $null }
        if ($null -ne $pushProtectionState) { Assert-SelectedRepositoryId -RepositoryId $pushProtectionState.id }
        if ($null -eq $pushProtectionState -or $pushProtectionState.full_name -isnot [string] -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals($pushProtectionState.full_name, "OWNER/REPO") -or
            $pushProtectionState.security_and_analysis.secret_scanning.status -isnot [string] -or
            $pushProtectionState.security_and_analysis.secret_scanning.status -cne "enabled" -or
            $pushProtectionState.security_and_analysis.secret_scanning_push_protection.status -isnot [string] -or
            $pushProtectionState.security_and_analysis.secret_scanning_push_protection.status -cne "enabled") {
          Write-Warning "Push protection and its secret-scanning prerequisite were not both verified as enabled for OWNER/REPO."
        }
      }
    }
  }
  ```

- **CodeQL advanced setup:** nếu người dùng chọn tường minh cấu hình do
  repository quản lý, chạy `advanced_codeql_preflight.py` đi kèm trước khi cài
  `assets/workflows/codeql.yml`. Cần GitHub Actions; repository private/internal
  cần GitHub Code Security. Script ủy quyền workflow/analysis/external-uploader
  inspection có giới hạn cho CodeQL preflight hiện có. Render default branch đã
  xác minh qua `{{REPO_SCAFFOLD_DEFAULT_BRANCH_GLOB_JSON_ESCAPED}}` cùng ngôn ngữ
  được hỗ trợ đã phát hiện; giữ CodeQL default setup ở trạng thái not configured.
  Không âm thầm cài uploader advanced thứ hai. Nếu default setup đã cấu hình,
  dừng và xin phê duyệt rõ trước khi chuyển mode.

  CodeQL advanced setup, dependency review và Scorecard kiểm tra
  `security_and_analysis.code_security.status` trên private/internal repository.
  Chỉ dùng `advanced_security.status` cũ khi không có `code_security`; giá trị bị
  tắt hoặc malformed tường minh không được fallback sang legacy evidence.

  ```powershell
  if (-not (Get-Variable REPO_ROOT -ErrorAction SilentlyContinue)) {
    throw "REPO_ROOT must be the surveyed target repository root before inspecting CodeQL setup."
  }
  if ([string]::IsNullOrWhiteSpace($DEFAULT_BRANCH)) {
    throw "DEFAULT_BRANCH must be known before inspecting CodeQL setup."
  }
  $advancedCodeqlPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/advanced_codeql_preflight.py"
  if (-not (Test-Path -LiteralPath $advancedCodeqlPreflight -PathType Leaf)) {
    throw "The bundled advanced CodeQL preflight is missing; do not copy codeql.yml."
  }
  # Set this to $true only after the user explicitly confirms that no external
  # or indirect process uploads CodeQL results.
  $advancedCodeqlNoExternalConfirmed = $false
  if ($advancedCodeqlNoExternalConfirmed -isnot [bool] -or -not $advancedCodeqlNoExternalConfirmed) {
    throw "Explicit confirmation of no external or indirect CodeQL uploader is required; do not copy codeql.yml."
  }
  $advancedCodeqlOutput = python $advancedCodeqlPreflight `
    --repo-root $REPO_ROOT `
    --repository "OWNER/REPO" `
    --expected-repository-id $SELECTED_REPOSITORY_ID `
    --default-branch $DEFAULT_BRANCH `
    --hostname "github.com" `
    --confirm-no-external-codeql 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Advanced CodeQL inspection is inconclusive; do not copy codeql.yml. $($advancedCodeqlOutput | Out-String)"
  }
  $advancedCodeqlText = $advancedCodeqlOutput | Out-String
  if (-not $advancedCodeqlText.TrimStart().StartsWith("{")) { throw "Advanced CodeQL preflight must be an object." }
  $advancedCodeqlResult = $advancedCodeqlText | ConvertFrom-Json -ErrorAction Stop
  Assert-SelectedRepositoryId -RepositoryId $advancedCodeqlResult.repository_id
  if ($advancedCodeqlResult -isnot [pscustomobject] -or
      $advancedCodeqlResult.repository -isnot [string] -or
      $advancedCodeqlResult.decision -isnot [string] -or
      $advancedCodeqlResult.default_branch -isnot [string] -or
      $advancedCodeqlResult.inspection_complete -isnot [bool] -or
      -not $advancedCodeqlResult.inspection_complete -or
      $advancedCodeqlResult.decision -ne "may-install-advanced-codeql-workflow") {
    throw "Advanced CodeQL setup is not eligible. Resolve the returned decision and rerun before copying codeql.yml."
  }
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
      [string]$advancedCodeqlResult.repository, "OWNER/REPO"
    ) -or [string]$advancedCodeqlResult.default_branch -cne $DEFAULT_BRANCH) {
    throw "Advanced CodeQL preflight result is not bound to OWNER/REPO and DEFAULT_BRANCH; do not copy codeql.yml."
  }
  ```

  Sau đó chạy `workflow_installation_preflight.py` trên `codeql.yml`; gate riêng
  này đối chiếu exact action pin với Actions policy hiệu lực trước khi copy asset.
  Default/advanced inspection lồng phải trả cùng numeric target ID và exact
  default branch; child verdict target khác không cho phép asset. Nested read
  dùng chung API request/byte/deadline budget; trạng thái sở hữu repository/
  Actions/default setup cuối giữ nguyên. Evidence chưa bind/drift cấm cài.
  Preserve configured-default có thể để workflow/analysis/admin evidence chưa
  biết; `null` không chứng minh inspection đã pass.

- **Scorecard SARIF upload:** `scorecard.yml` tải SARIF result bên thứ ba lên code
  scanning. GitHub mô tả tại [Uploading a SARIF file to GitHub](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/integrate-with-existing-tools/upload-sarif-file):
  repository công khai đủ điều kiện; private/internal cần target thuộc tổ chức
  và bật GitHub Code Security. Trước khi copy asset, chạy capability preflight
  đi kèm; chỉ tiếp tục khi trả `may-install-scorecard-workflow`, rồi chạy
  workflow-installation preflight cho exact action pin.

  ```powershell
  $scorecardPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/scorecard_preflight.py"
  if (-not (Test-Path -LiteralPath $scorecardPreflight -PathType Leaf)) {
    throw "The bundled Scorecard preflight is missing; do not copy scorecard.yml."
  }
  $scorecardPreflightOutput = python $scorecardPreflight --repository "OWNER/REPO" `
    --expected-repository-id $SELECTED_REPOSITORY_ID 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Scorecard preflight was inconclusive; do not copy scorecard.yml. $($scorecardPreflightOutput | Out-String)"
  }
  $scorecardPreflightText = $scorecardPreflightOutput | Out-String
  if (-not $scorecardPreflightText.TrimStart().StartsWith("{")) {
    throw "Scorecard preflight must return a JSON object, not an array or scalar."
  }
  $scorecardPreflightResult = $scorecardPreflightText | ConvertFrom-Json
  if ($scorecardPreflightResult -isnot [System.Management.Automation.PSCustomObject]) {
    throw "Scorecard preflight returned a non-object verdict; do not copy scorecard.yml."
  }
  Assert-SelectedRepositoryId -RepositoryId $scorecardPreflightResult.repository_id
  if ($scorecardPreflightResult.inspection_complete -isnot [bool] -or
      -not $scorecardPreflightResult.inspection_complete -or
      $scorecardPreflightResult.decision -isnot [string] -or
      $scorecardPreflightResult.decision -ne "may-install-scorecard-workflow") {
    throw "Scorecard is not eligible. Resolve the returned decision and rerun before copying scorecard.yml."
  }
  if ($scorecardPreflightResult.repository -isnot [string] -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$scorecardPreflightResult.repository, "OWNER/REPO"
  )) {
    throw "Scorecard preflight returned a different repository; do not copy scorecard.yml."
  }
  ```

- **Code scanning default setup:** cần GitHub Actions và đủ điều kiện code
  scanning. GitHub có thể bật default setup khi chưa có ngôn ngữ được hỗ trợ,
  nhưng scan không chạy cho đến khi thêm ngôn ngữ đó; giải thích rõ khác biệt,
  không tuyên bố có scan coverage chỉ từ trạng thái cấu hình. Bỏ mutation path
  này nếu đã chọn advanced workflow do repository quản lý. Nếu không, trước tiên
  inspect default setup hiện tại, workflow evidence trực tiếp trong working tree
  và default branch, cùng CodeQL analysis đã có. Hỏi riêng có external CI,
  indirect script, local/composite action hoặc process nào khác upload CodeQL
  result không. Không suy ra không có chỉ từ việc xem workflow repository.
  Yêu cầu chung bật code scanning không cho phép thay advanced setup: chuyển mode
  sẽ tắt workflow đó và chặn CodeQL analysis API upload.

  Preflight đi kèm cần PyYAML và Python feature release ít nhất bằng
tooling-python-minimum trong .github/ci-toolchain.json. Nếu default setup chưa
cấu hình, mutation-mode check yêu cầu repository admin, repository active,
Actions bật và đúng default branch hiện tại trước khi inspect workflow remote.
Remote tree có symlink hoặc mode không phải file bị fail closed, không parse thành
workflow YAML. Reusable workflow được lấy dưới dạng regular file từ immutable
Git tree, không dùng Contents API có thể dereference symlink.

Giới hạn tree work trước khi lập chỉ mục path; từ chối entry partial/nhập nhằng;
xác minh raw Git blob byte trước parse hoặc cache signal. Chỉ cache trong cùng
inspection với chính xác repository/commit/path identity; read lỗi không được
tái dùng làm bằng chứng không có file. Root inventory của default branch cũng
kiểm tra path có type đúng và tổng entry; entry lạ không được biến mất để kết
luận không có advanced workflow. Inventory thiếu là inconclusive trước mutation.

Full literal commit pin của reusable workflow phải trùng commit đã resolve trước
tree/blob read hoặc dùng ref/signal cache. Không đổi pin sang SHA khác dù hợp
định dạng. ID so sánh không phân biệt hoa thường; symbolic branch/tag ref được
hỗ trợ vẫn resolve đến snapshot trả về. Binding này không chứng minh commit
thuộc repository hoặc workflow đã được thực thi.

Structural preflight dùng PyYAML BaseLoader không coercion, từ chối key trùng,
chỉ inspect file trực tiếp trong .github/workflows, semantic jobs.*.uses,
jobs.*.steps[*].uses và executable run content có phân tích shell; tôn trọng
shell chọn ở step/job/workflow. Với Bash/PowerShell đã nhận diện, bỏ qua
heredoc/here-string/arithmetic-shift/literal/comment và function không được gọi,
kể cả function có brace mở ở dòng kế tiếp. Giữ lại function body được gọi bắc
cầu, literal eval/trap handler, exported function gọi từ nested-shell literal
command, alias Bash/PowerShell resolve tĩnh, shell heredoc trực tiếp, wrapper đã
nhận diện, GNU env split string, xargs với option GNU/BSD hỗ trợ, find executor
trực tiếp, shell -c, shell nhận pipeline, backtick/$() command substitution,
Bash process substitution, PowerShell scriptblock/nested -Command,
Invoke-Expression, Start-Process, CodeQL command trực tiếp qua cmd /c hoặc /k,
quoted call-operator và PowerShell $() execution.

Command position không resolve được, call-operator expression, dynamic executor
hoặc alias target đã nhận diện, encoded PowerShell command có payload không
literal, construct malformed/chưa đóng đều fail closed. Effective shell không
hỗ trợ hoặc không resolve được cũng fail closed, không fallback sang raw-text
inspection. Nếu default setup đã cấu hình, preflight trả preserve decision an
toàn, bỏ workflow/analysis query không cần, đặt evidence chưa inspect thành null,
workflow_inspection_performed và analysis_inspection_performed là false. State
khác phải đúng not-configured; state không biết fail closed. Preflight theo
reusable workflow theo từng top-level caller, từ chối cycle, thực thi giới hạn
GitHub 50 workflow được gọi duy nhất và 10 level trên mọi call path, giữ thêm
trần traversal 500 edge, giới hạn API request và đặt timeout cho từng gh api
subprocess. Thiếu Python, PyYAML, effective shell, shell syntax, workflow,
linked path, API response hoặc xác nhận riêng không có CodeQL uploader ngoài/
gián tiếp thì kết quả inconclusive và cấm mutation.

Khởi tạo noExternalCodeqlConfirmed là false. Chỉ đặt true sau khi người dùng
xác nhận rõ không có external CI, indirect script, local/composite action hoặc
process khác upload CodeQL result. Xác nhận này tách biệt với chấp thuận scaffold
chung và phê duyệt riêng để chuyển từ advanced setup.

  ```powershell
  if (-not (Get-Variable REPO_ROOT -ErrorAction SilentlyContinue)) {
    throw "REPO_ROOT must be the surveyed target repository root before inspecting CodeQL setup."
  }
  if (-not (Get-Variable REPO_SCAFFOLD_SKILL_ROOT -ErrorAction SilentlyContinue)) {
    throw "REPO_SCAFFOLD_SKILL_ROOT must identify the directory containing this skill's SKILL.md."
  }
  if ([string]::IsNullOrWhiteSpace($DEFAULT_BRANCH)) {
    throw "DEFAULT_BRANCH must be known before inspecting CodeQL setup."
  }

  $preflightScript = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/codeql_preflight.py"
  if (-not (Test-Path -LiteralPath $preflightScript -PathType Leaf)) {
    throw "The bundled CodeQL preflight script is missing; do not PATCH default setup."
  }
  $toolchainPolicyPath = Join-Path $REPO_ROOT ".github/ci-toolchain.json"
  if (-not (Test-Path -LiteralPath $toolchainPolicyPath -PathType Leaf)) {
    throw "The CI toolchain policy is missing; do not PATCH default setup."
  }
  try {
    $toolchainPolicy = Get-Content -LiteralPath $toolchainPolicyPath -Raw -Encoding UTF8 |
      ConvertFrom-Json
    $minimumPython = $toolchainPolicy.'tooling-python-minimum'
  } catch {
    throw "The CI toolchain policy is invalid; do not PATCH default setup."
  }
  if ($minimumPython -notmatch '^3\.(0|[1-9][0-9]*)$') {
    throw "The tooling-python-minimum policy value is invalid; do not PATCH default setup."
  }
  $pythonCommand = $null
  foreach ($pythonName in @("python3", "python")) {
    $candidate = Get-Command $pythonName -ErrorAction SilentlyContinue
    if ($null -eq $candidate) { continue }
    & $candidate.Source -c "import sys, yaml; required=tuple(map(int, sys.argv[1].split('.'))); raise SystemExit(0 if sys.version_info[:2] >= required else 1)" $minimumPython 2>$null
    if ($LASTEXITCODE -eq 0) {
      $pythonCommand = $candidate
      break
    }
  }
  $defaultSetupEnablementApproved = $false
  $defaultSetupSwitchApproved = $false
  $noExternalCodeqlConfirmed = $false

  if ($null -eq $pythonCommand) {
    Write-Warning "No Python $minimumPython or newer interpreter with PyYAML is available for structural workflow inspection; do not PATCH default setup."
  } else {
    $preflightArguments = @(
      "--repo-root", $REPO_ROOT,
      "--repository", "OWNER/REPO",
      "--expected-repository-id", $SELECTED_REPOSITORY_ID,
      "--default-branch", $DEFAULT_BRANCH,
      "--hostname", "github.com",
      "--require-administration-permission"
    )
    if ($noExternalCodeqlConfirmed) {
      $preflightArguments += "--confirm-no-external-codeql"
    }
    $preflightOutput = & $pythonCommand.Source $preflightScript @preflightArguments 2>&1
    $preflightExitCode = $LASTEXITCODE
    if ($preflightExitCode -ne 0) {
      Write-Warning "CodeQL setup inspection is inconclusive; do not PATCH. $($preflightOutput | Out-String)"
    } else {
      try {
        $preflightText = $preflightOutput | Out-String
        if (-not $preflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
        $preflight = $preflightText | ConvertFrom-Json -ErrorAction Stop
      } catch {
        throw "CodeQL preflight returned invalid JSON; do not PATCH."
      }
      Assert-SelectedRepositoryId -RepositoryId $preflight.repository_id
      if ($preflight -isnot [pscustomobject] -or
          $preflight.repository -isnot [string] -or $preflight.default_branch -isnot [string] -or
          $preflight.decision -isnot [string] -or $preflight.default_setup_state -isnot [string]) {
        throw "CodeQL preflight has an invalid identity, branch or decision schema; do not PATCH."
      }
      if ($preflight.inspection_complete -is [bool] -and $preflight.inspection_complete -and
          $preflight.decision -cin @("may-offer-default-setup", "require-explicit-switch-confirmation")) {
        if ($preflight.advanced_workflows -isnot [Array] -or
            @($preflight.advanced_workflows | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
            $preflight.has_codeql_analysis -isnot [bool] -or
            $preflight.workflow_inspection_performed -isnot [bool] -or -not $preflight.workflow_inspection_performed -or
            $preflight.analysis_inspection_performed -isnot [bool] -or -not $preflight.analysis_inspection_performed -or
            $preflight.administration_permission -isnot [bool] -or -not $preflight.administration_permission -or
            $preflight.github_actions_enabled -isnot [bool] -or -not $preflight.github_actions_enabled -or
            $preflight.remote_inspection_commit -isnot [string] -or
            $preflight.remote_inspection_commit -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') {
          throw "CodeQL mutation evidence is incomplete or malformed; do not offer or cache it as an approved plan."
        }
      }
      if ($preflight.inspection_complete -isnot [bool] -or
          -not $preflight.inspection_complete) {
        Write-Warning "CodeQL setup inspection is inconclusive; do not PATCH."
      } elseif (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
          [string]$preflight.repository, "OWNER/REPO"
        ) -or [string]$preflight.default_branch -cne $DEFAULT_BRANCH) {
        throw "CodeQL preflight result is not bound to OWNER/REPO and DEFAULT_BRANCH; do not PATCH."
      } elseif ($preflight.decision -ne "preserve-default-setup" -and
          ($preflight.administration_permission -ne $true -or
           $preflight.github_actions_enabled -ne $true)) {
        Write-Warning "CodeQL default setup lacks confirmed administration permission or enabled Actions; do not PATCH."
      } elseif ($preflight.decision -eq "preserve-default-setup") {
        Write-Output "Code scanning default setup is already configured; preserve it without PATCHing."
      } elseif ($preflight.decision -eq "require-explicit-switch-confirmation") {
        $workflowSummary = @($preflight.advanced_workflows) -join ", "
        Write-Warning "Possible CodeQL advanced setup detected ($workflowSummary). Stop before PATCH, explain that default setup will disable the existing workflow and block CodeQL analysis API uploads, and ask for explicit approval to switch."
      } elseif ($preflight.decision -eq "may-offer-default-setup") {
        Write-Output "Default setup is eligible to be offered. Obtain the user's explicit approval before setting `$defaultSetupEnablementApproved or PATCHing."
      } else {
        Write-Warning "CodeQL preflight returned an unknown decision; do not PATCH."
      }
      $initialCodeqlDecision = $null
      $initialAdvancedWorkflows = $null
      $initialHasCodeqlAnalysis = $null
      $initialRemoteCodeqlCommit = $null
      if ($preflight.inspection_complete -is [bool] -and $preflight.inspection_complete) {
        $initialCodeqlDecision = $preflight.decision
        if ($preflight.decision -cin @("may-offer-default-setup", "require-explicit-switch-confirmation")) {
          $initialAdvancedWorkflows = @($preflight.advanced_workflows)
          $initialHasCodeqlAnalysis = $preflight.has_codeql_analysis
          $initialRemoteCodeqlCommit = $preflight.remote_inspection_commit
        }
      }
    }
  }
  ```

  Chỉ chạy block mutation dưới đây sau khi người dùng duyệt rõ việc bật CodeQL
  default setup cho repository. Đồng ý scaffold chung hoặc read-only decision
  `may-offer-default-setup` không phải approval này. Nếu preflight trả
  `require-explicit-switch-confirmation`, giải thích advanced workflow và analysis
  evidence chính xác, xin duyệt riêng để chuyển mode, rồi mới đặt
  `$defaultSetupSwitchApproved = $true`.

  Ngay trước PATCH, chạy lại cùng preflight với target/branch như cũ và xác nhận
  tường minh không có uploader ngoài; cần decision/evidence giống nhau, bind
  chính xác repository/default branch, state `not-configured`, quyền admin hiện
  tại. Decision/evidence thay đổi cần review và approval mới. Bind cả
  `remote_inspection_commit` cuối với proof ban đầu; branch head đổi làm absence
  evidence mất hiệu lực dù tên branch và workflow list không đổi.

  PATCH có thể trả `202 Accepted` kèm validation workflow; đợi workflow hoàn
  tất và yêu cầu GET cuối có `state: configured` trước khi báo feature đã bật.
  Validation conclusion không thành công không phủ định `state: configured`;
  báo conclusion riêng và không tuyên bố scan đã thành công:

  ```powershell
  if ($defaultSetupEnablementApproved -isnot [bool] -or -not $defaultSetupEnablementApproved) {
    throw "CodeQL default setup was not explicitly approved; do not PATCH."
  }
  if ($initialCodeqlDecision -eq "preserve-default-setup" -or
      $initialCodeqlDecision -notin @("may-offer-default-setup", "require-explicit-switch-confirmation")) {
    throw "The initial CodeQL preflight did not permit this mutation."
  }
  if ($initialCodeqlDecision -eq "require-explicit-switch-confirmation" -and
      ($defaultSetupSwitchApproved -isnot [bool] -or -not $defaultSetupSwitchApproved)) {
    throw "Switching from existing CodeQL setup was not separately approved; do not PATCH."
  }
  if ($noExternalCodeqlConfirmed -isnot [bool] -or -not $noExternalCodeqlConfirmed) {
    throw "Absence of external or indirect CodeQL uploaders was not confirmed; do not PATCH."
  }
  $finalPreflightOutput = & $pythonCommand.Source $preflightScript `
    --repo-root $REPO_ROOT `
    --repository "OWNER/REPO" `
    --expected-repository-id $SELECTED_REPOSITORY_ID `
    --default-branch $DEFAULT_BRANCH `
    --hostname "github.com" `
    --confirm-no-external-codeql `
    --require-administration-permission 2>&1
  $finalPreflightExitCode = $LASTEXITCODE
  if ($finalPreflightExitCode -ne 0) {
    throw "Final CodeQL preflight is inconclusive; do not PATCH. $($finalPreflightOutput | Out-String)"
  }
  try {
    $finalPreflightText = $finalPreflightOutput | Out-String
    if (-not $finalPreflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
    $finalPreflight = $finalPreflightText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Final CodeQL preflight returned invalid JSON; do not PATCH."
  }
  Assert-SelectedRepositoryId -RepositoryId $finalPreflight.repository_id
  if ($finalPreflight -isnot [pscustomobject] -or
      $finalPreflight.repository -isnot [string] -or $finalPreflight.default_branch -isnot [string] -or
      $finalPreflight.decision -isnot [string] -or $finalPreflight.default_setup_state -isnot [string] -or
      $finalPreflight.advanced_workflows -isnot [Array] -or
      @($finalPreflight.advanced_workflows | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
      $finalPreflight.has_codeql_analysis -isnot [bool] -or
      $finalPreflight.administration_permission -isnot [bool] -or
      $finalPreflight.github_actions_enabled -isnot [bool] -or
      $finalPreflight.external_codeql_absence_confirmed -isnot [bool] -or
      $finalPreflight.workflow_inspection_performed -isnot [bool] -or -not $finalPreflight.workflow_inspection_performed -or
      $finalPreflight.analysis_inspection_performed -isnot [bool] -or -not $finalPreflight.analysis_inspection_performed -or
      $finalPreflight.remote_inspection_commit -isnot [string] -or
      $finalPreflight.remote_inspection_commit -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') {
    throw "Final CodeQL mutation evidence is incomplete or malformed; do not PATCH."
  }
  $finalAdvancedWorkflows = @($finalPreflight.advanced_workflows)
  if ($finalPreflight.inspection_complete -isnot [bool] -or
      -not $finalPreflight.inspection_complete -or
      $finalPreflight.decision -cne $initialCodeqlDecision -or
      $finalPreflight.default_setup_state -cne "not-configured" -or
      $finalPreflight.external_codeql_absence_confirmed -ne $true -or
      $finalPreflight.administration_permission -ne $true -or
      $finalPreflight.github_actions_enabled -ne $true -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$finalPreflight.repository, "OWNER/REPO"
      ) -or [string]$finalPreflight.default_branch -cne $DEFAULT_BRANCH -or
      $finalPreflight.has_codeql_analysis -ne $initialHasCodeqlAnalysis -or
      $finalPreflight.remote_inspection_commit -isnot [string] -or
      $finalPreflight.remote_inspection_commit -cne $initialRemoteCodeqlCommit -or
      $finalAdvancedWorkflows.Count -ne $initialAdvancedWorkflows.Count -or
      $null -ne (Compare-Object -ReferenceObject $initialAdvancedWorkflows -DifferenceObject $finalAdvancedWorkflows -CaseSensitive)) {
    throw "CodeQL evidence or target changed since approval; review the fresh result and obtain any required approval again."
  }
  if ($finalPreflight.decision -eq "require-explicit-switch-confirmation" -and
      ($defaultSetupSwitchApproved -isnot [bool] -or -not $defaultSetupSwitchApproved)) {
    throw "The fresh CodeQL result requires separate approval to switch; do not PATCH."
  }
  $defaultSetupPath = "repos/OWNER/REPO/code-scanning/default-setup"
  $defaultSetupEnabled = $false
  $observedDefaultSetupState = $null
  $validationFinished = $false
  $validationPollingFailed = $false
  $validationConclusion = $null
  $validationRunId = $null
  $setupOutput = & gh api --hostname github.com --include -X PATCH $defaultSetupPath -f state=configured 2>&1
  $setupExitCode = $LASTEXITCODE
  try {
    if ($setupExitCode -ne 0) {
      throw "Default-setup PATCH did not acknowledge success. State may have changed; do not replay or roll back automatically."
    }
    $setupText = $setupOutput | Out-String
    if ([Text.Encoding]::UTF8.GetByteCount($setupText) -gt 2097152 -or
        $setupText -notmatch '(?s)\AHTTP/[0-9]+(?:\.[0-9]+)? (200|202)[^\r\n]*\r?\n.*?\r?\n\r?\n(?<body>.*)\z') {
      throw "Default-setup acknowledgment has an invalid or oversized HTTP envelope."
    }
    $setupStatus = $Matches[1]
    $setupBody = $Matches['body'].Trim()
    $setupResponse = $null
    if ($setupBody.Length -gt 0) {
      $setupResponse = $setupBody | ConvertFrom-Json -ErrorAction Stop
      if ($setupResponse -isnot [pscustomobject]) {
        throw "Default-setup acknowledgment must be a JSON object."
      }
    }
    if ($null -ne $setupResponse -and $null -ne $setupResponse.run_id) {
      if (($setupResponse.run_id -isnot [int] -and $setupResponse.run_id -isnot [long]) -or
          $setupResponse.run_id -le 0) {
        throw "Default-setup validation run_id must be a positive JSON integer."
      }
      $validationRunId = $setupResponse.run_id
      # One bounded polling batch; retain the exact ID for later observation.
      for ($attempt = 0; $attempt -lt 4; $attempt++) {
        $runOutput = gh api --hostname github.com "repos/OWNER/REPO/actions/runs/$validationRunId" 2>&1
        if ($LASTEXITCODE -ne 0) {
          throw "Could not query the recorded default-setup validation run."
        }
        $runText = $runOutput | Out-String
        if ([Text.Encoding]::UTF8.GetByteCount($runText) -gt 2097152) {
          throw "Default-setup validation response exceeds the inspection limit."
        }
        $run = $runText | ConvertFrom-Json -ErrorAction Stop
        if ($run -isnot [pscustomobject] -or
            ($run.id -isnot [int] -and $run.id -isnot [long]) -or
            $run.id -ne $validationRunId -or
            $run.repository -isnot [pscustomobject] -or
            $run.repository.full_name -isnot [string] -or
            -not [StringComparer]::OrdinalIgnoreCase.Equals($run.repository.full_name, "OWNER/REPO")) {
          throw "Validation run is not bound to the recorded run ID and selected repository."
        }
        Assert-SelectedRepositoryId -RepositoryId $run.repository.id
        if ($run.status -isnot [string] -or $run.status -cnotin @(
            "queued", "in_progress", "completed", "waiting", "requested", "pending")) {
          throw "Default-setup validation status is unknown or malformed."
        }
        if ($run.status -ceq "completed") {
          if ($run.conclusion -isnot [string] -or $run.conclusion -cnotin @(
              "success", "failure", "neutral", "cancelled", "timed_out", "action_required", "stale", "skipped", "startup_failure")) {
            throw "Completed validation run has no recognized conclusion."
          }
          $validationFinished = $true
          $validationConclusion = $run.conclusion
          break
        }
        if ($null -ne $run.conclusion) {
          throw "Nonterminal validation run has a contradictory conclusion."
        }
        if ($attempt -lt 3) { Start-Sleep -Seconds 10 }
      }
      if (-not $validationFinished) {
        Write-Warning "Validation run $validationRunId is nonterminal. Preserve this ID and resume observation; do not repeat PATCH or claim successful validation."
      }
    } elseif ($setupStatus -ceq "200") {
      $validationFinished = $true
    } else {
      throw "An asynchronous 202 acknowledgment requires a positive validation run_id."
    }
  } catch {
    $validationPollingFailed = $true
    Write-Warning "Default-setup acknowledgment or validation is unverified. $($_.Exception.Message)"
  } finally {
    # Observe current state after every attempt, including failed acknowledgment or polling.
    # Bracket the state read because its response has no immutable repository identity.
    try {
      $beforeOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
      if ($LASTEXITCODE -ne 0) { throw "Repository identity is unavailable before readback." }
      $before = ($beforeOutput | Out-String) | ConvertFrom-Json -ErrorAction Stop
      Assert-SelectedRepositoryId -RepositoryId $before.id
      if ($before.full_name -isnot [string] -or
          -not [StringComparer]::OrdinalIgnoreCase.Equals($before.full_name, "OWNER/REPO") -or
          $before.default_branch -isnot [string] -or $before.default_branch -cne $DEFAULT_BRANCH) {
        throw "Repository name or default branch changed before readback."
      }
      $verifyOutput = gh api --hostname github.com $defaultSetupPath 2>&1
      if ($LASTEXITCODE -ne 0) { throw "Final default-setup state could not be queried." }
      $verifyText = $verifyOutput | Out-String
      if ([Text.Encoding]::UTF8.GetByteCount($verifyText) -gt 2097152) {
        throw "Default-setup readback exceeds the inspection limit."
      }
      $verifiedSetup = $verifyText | ConvertFrom-Json -ErrorAction Stop
      if ($verifiedSetup -isnot [pscustomobject] -or $verifiedSetup.state -isnot [string] -or
          $verifiedSetup.state -cnotin @("configured", "not-configured")) {
        throw "Default-setup readback has an unknown or malformed state."
      }
      $afterOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
      if ($LASTEXITCODE -ne 0) { throw "Repository identity is unavailable after readback." }
      $after = ($afterOutput | Out-String) | ConvertFrom-Json -ErrorAction Stop
      Assert-SelectedRepositoryId -RepositoryId $after.id
      if ($after.full_name -isnot [string] -or
          -not [StringComparer]::OrdinalIgnoreCase.Equals($after.full_name, "OWNER/REPO") -or
          $after.default_branch -isnot [string] -or $after.default_branch -cne $DEFAULT_BRANCH) {
        throw "Repository name or default branch changed after readback."
      }
      $observedDefaultSetupState = $verifiedSetup.state
      Write-Output "Observed default-setup state: $observedDefaultSetupState. This observation is separate from acknowledgment and scan success."
      $defaultSetupEnabled = $observedDefaultSetupState -ceq "configured" -and
        $validationFinished -and -not $validationPollingFailed
    } catch {
      Write-Warning "Post-attempt default-setup state is unverified. $($_.Exception.Message)"
    }
  }
  if ($null -ne $validationConclusion -and $validationConclusion -cne "success") {
    Write-Warning "Default-setup validation concluded '$validationConclusion'; report this separately and do not claim successful scans."
  }
  if ($defaultSetupEnabled) {
    Write-Output "Code scanning default setup is enabled and verified."
  }
  ```

  Nếu bounded polling batch thấy validation chưa terminal, giữ typed
  `$validationRunId` và báo progress. Lần quan sát sau lặp lại check exact run ID
  và numeric repository ID ở trên, rồi đọc lại default setup trong identity
  bracket bất kể conclusion. Không phát lại PATCH để xin run khác. Luôn quan sát
  state sau acknowledgment fail/ambiguous/malformed/unsuccessful, nhưng không
  coi readback là bằng chứng request được quy về đúng lệnh hoặc validation thành
  công. State configured khi validation pending/unverified chỉ báo cấu hình hiện
  tại, không phải enablement verification hoàn tất. REST read theo tên không
  xóa được race thay thế xen giữa; identity bracketing chỉ phát hiện drift đã
  quan sát, không atomic.

- **Private vulnerability reporting:** dù tên gọi như vậy, setting này nhận báo
  cáo riêng tư trên repository công khai. Đề xuất cho repository công khai đã
  xác minh, kể cả fork, với quyền admin hiện tại. [Hợp đồng availability của
  GitHub](https://docs.github.com/en/code-security/concepts/vulnerability-reporting-and-management/repository-security-advisories)
  không cấm mọi fork. Giữ explicit approval cho feature và kiểm tra sau attempt
  ở dưới; preflight eligibility không chứng minh API write thành công:

  ```powershell
  if ($enablePrivateVulnerabilityReportingRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-private-vulnerability-reporting") `
      -ExpectedFeature "private_vulnerability_reporting"
    if ($null -ne $featurePreflight) {
      $enableOutput = gh api --hostname github.com -X PUT `
        repos/OWNER/REPO/private-vulnerability-reporting 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Private vulnerability reporting write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com `
        repos/OWNER/REPO/private-vulnerability-reporting 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Private vulnerability reporting could not be verified after the attempted write. $($verifyOutput | Out-String)"
      } else {
        try { $reportingState = ($verifyOutput | Out-String) | ConvertFrom-Json } catch { $reportingState = $null }
        if ($null -eq $reportingState -or $reportingState.enabled -isnot [bool] -or
            $reportingState.enabled -ne $true) {
          Write-Warning "Private vulnerability reporting is not verified as enabled after the attempted write."
        }
      }
    }
  }
  ```

- **Dependency review workflow:** trước khi cài
  `assets/workflows/dependency-review.yml`, chạy preflight đi kèm. Nó chứng minh
  dependency graph trả về SPDX 2.x API envelope có cấu trúc đầy đủ, giới hạn
  kích thước, gồm creation metadata và typed package record. Không chứng nhận
  dependency resolution đầy đủ, vulnerability result hiện tại, tuân thủ license
  hoặc nội dung repository revision tương lai. Document name không cần trùng
  repository slug; package array rỗng và package version tùy chọn hợp lệ.
  Repository công khai chỉ tiếp tục khi có proof đó; private/internal còn cần
  thuộc tổ chức và bật GitHub Code Security. Response thiếu/malformed là
  inconclusive, cấm cài. Chạy workflow-installation preflight nữa vì nó kiểm tra
  độc lập Actions policy và exact action pin.

  GitHub client dùng chung giới hạn stdout/stderr giữ lại ngay trong lúc capture,
  trước ghi temporary file, và kiểm tra byte budget inspection còn lại trước
  command CLI tiếp theo. Cần EOF cả hai pipe và process hoàn tất; spool quá lớn,
  timeout hoặc chưa đủ không phải verdict. Tắt forced terminal output, paging,
  debug log cho riêng child invocation mà không đổi cấu hình CLI toàn cục của
  user. Thu hồi CLI process thuộc sở hữu và đóng pipe phía parent khi từ chối.
  Điều này không chứng minh memory/network limit bên trong SDK, dọn process con,
  HTTP origin ban đầu hoặc số request thực tế từ gh.

  Gh-backed verdict ghi `github_client_requests` là client request attempt.
  Field cũ `github_api_requests` chỉ giữ làm compatibility alias, có tag
  `github_api_requests_unit: client-request-attempts`; không phải network count
  chính xác. `github_http_requests` là null với state
  `github_http_requests_state: not-measured`. Transport work chưa biết không phải
  zero, not-applicable hoặc quota evidence đạt. Không dùng diagnostics đó thay
  kiểm tra target/state/provenance/quyền mutation.

  **Ranh giới tương thích SBOM API:** helper hiện dùng synchronous
  [`dependency-graph/sbom` export](https://docs.github.com/en/rest/dependency-graph/sboms#export-a-software-bill-of-materials-sbom-for-a-repository).
  Cần GitHub CLI báo HTTP `200` và SPDX envelope đã xác minh; receipt `201`,
  `202`, `204`, `206` hoặc redirect không thay export hoàn tất. Điều này kiểm
  tra response quan sát được, không attestation HTTP response ban đầu hoặc
  redirect origin. GitHub đã deprecate endpoint và dự kiến bỏ sau 13-11-2026.
  Helper chưa triển khai async replacement. Export không khả dụng/malformed là
  inconclusive và chặn copy workflow; không gắn nhãn inspection bắt buộc là
  not-applicable hoặc pass.

  Chuyển sang async cần authorization riêng cho GET `generate-report` tạo job,
  bind đúng repository/report và resume có giới hạn cùng report trả về thay vì
  tạo lại. Generation trả `201`; fetch trả `202` rỗng khi pending và `302` khi
  sẵn sàng. Report có thể lưu tối đa một tuần; từng temporary download URL hết
  hạn riêng. Thời hạn này không phải verdict freshness hoặc bằng chứng cho
  revision repository tương lai. Không chuyển GitHub credential đến download
  destination và không coi generation acknowledgment là download/validation
  thành công. Lệnh dưới không thực hiện async operation. Thời điểm hết hạn mỗi
  URL bắt đầu tại fetch; migration phải xác minh freshness report và URL riêng.
  Ngày review tài liệu không chứng minh migration đã được triển khai.

  ```powershell
  $dependencyReviewPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/dependency_review_preflight.py"
  if (-not (Test-Path -LiteralPath $dependencyReviewPreflight -PathType Leaf)) {
    throw "The bundled dependency-review preflight is missing; do not copy the workflow."
  }
  $dependencyReviewPreflightOutput = python $dependencyReviewPreflight `
    --repository "OWNER/REPO" --hostname "github.com" `
    --expected-repository-id $SELECTED_REPOSITORY_ID 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Dependency-review inspection is inconclusive; do not copy the workflow. $($dependencyReviewPreflightOutput | Out-String)"
  }
  $dependencyReviewPreflightText = $dependencyReviewPreflightOutput | Out-String
  if (-not $dependencyReviewPreflightText.TrimStart().StartsWith("{")) {
    throw "Dependency-review preflight must return a JSON object, not an array or scalar."
  }
  $dependencyReviewPreflightResult = $dependencyReviewPreflightText | ConvertFrom-Json
  if ($dependencyReviewPreflightResult -isnot [System.Management.Automation.PSCustomObject]) {
    throw "Dependency-review preflight returned a non-object verdict; do not copy the workflow."
  }
  Assert-SelectedRepositoryId -RepositoryId $dependencyReviewPreflightResult.repository_id
  if ($dependencyReviewPreflightResult.inspection_complete -isnot [bool] -or
      -not $dependencyReviewPreflightResult.inspection_complete -or
      $dependencyReviewPreflightResult.dependency_graph_available -isnot [bool] -or
      -not $dependencyReviewPreflightResult.dependency_graph_available -or
      $dependencyReviewPreflightResult.visibility -isnot [string] -or
      $dependencyReviewPreflightResult.visibility -notin @("public", "private", "internal") -or
      $dependencyReviewPreflightResult.github_code_security -isnot [string] -or
      ($dependencyReviewPreflightResult.visibility -eq "public" -and
       $dependencyReviewPreflightResult.github_code_security -ne "not-required") -or
      ($dependencyReviewPreflightResult.visibility -ne "public" -and
       $dependencyReviewPreflightResult.github_code_security -ne "enabled") -or
      $dependencyReviewPreflightResult.decision -isnot [string] -or
      $dependencyReviewPreflightResult.decision -ne "may-install-dependency-review-workflow") {
    throw "Dependency-review capability is not confirmed. Resolve the returned decision and rerun before copying the workflow."
  }
  if ($dependencyReviewPreflightResult.repository -isnot [string] -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$dependencyReviewPreflightResult.repository, "OWNER/REPO"
  )) {
    throw "Dependency-review preflight returned a different repository; do not copy the workflow."
  }
  ```

  Asset v5 xử lý cả `pull_request` và `merge_group` payload. Chỉ bắt buộc check
  `dependency-review` khi workflow chạy được trên mọi event mà effective rule
  của repository yêu cầu.

## Merge setting

Khớp PR flow mặc định squash và giữ branch gọn:

Trên GitHub.com, private repository chỉ có auto-merge với GitHub Pro, Team hoặc
Enterprise Cloud. Kiểm tra plan/capability trước. Coi `403` là forbidden; giữ
response `422` và báo cấu hình bị từ chối/không đủ điều kiện, không đoán token
permission gây ra lỗi. Không cài auto-merge workflow trừ khi bật thành công.

Trước merge-setting mutation đầu tiên, chạy bundled
`scripts/merge_settings_preflight.py`. Script chỉ đọc, fail-closed: bind response
với `OWNER/REPO` tường minh, từ chối repo archived/disabled, yêu cầu quyền admin
hiện tại và từ chối effective-rule result không giới hạn. Nó giữ mọi merge
method mà effective merge queue/PR rule yêu cầu, ghi chính xác method plan cuối;
đồng thời bind `delete_branch_on_merge=true` và
`squash_merge_commit_title=PR_TITLE` được yêu cầu cùng state hiện có. Trước mỗi
write setting riêng, chạy lại và so policy/value mới với intermediate state dự
kiến.

Mỗi inspection đọc lại đầy đủ effective-rule evidence và classic-protection
evidence dùng cho auto-merge applicability; từ chối drift kể cả khi context list
mới vẫn còn check. Nó đọc lại numeric repository identity, active/admin/default
branch và merge setting điều khiển trước verdict. Chỉ revalidate deletion/title
value được yêu cầu; optional value chưa inspect giữ null tường minh, không coi là
đã xác minh.

Preflight trả `require-explicit-merge-method-removal-confirmation` nếu squash-
default plan tắt merge/rebase method đang bật. Không truyền cờ confirmation đến
khi user duyệt riêng method cần bỏ. Nếu `--require-auto-merge-workflows` trả
`skip-auto-merge-workflows`, giữ merge plan nhưng không cài auto-merge asset đi
kèm. Nếu trả `enable-auto-merge-before-installing-workflows`, không cài asset:
chỉ bật capability sau approval riêng, xác minh mutation rồi chạy lại preflight.

Nếu trả `require-status-checks-before-installing-auto-merge-workflows`, không
cài asset nào: cấu hình ít nhất một effective required status check bằng thay
đổi branch policy riêng đã duyệt, xác minh rồi chạy lại preflight. Helper nhận
required check từ ruleset áp dụng hoặc classic protection, và fail closed nếu
một trong hai API response malformed.

```powershell
$mergeSettingsPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/merge_settings_preflight.py"
if (-not (Test-Path -LiteralPath $mergeSettingsPreflight -PathType Leaf)) {
  throw "The bundled merge-settings preflight is missing; do not mutate merge settings."
}
if ([string]::IsNullOrWhiteSpace($DEFAULT_BRANCH)) {
  throw "DEFAULT_BRANCH must be known before inspecting merge settings."
}
$preflightArguments = @(
  "--repository", "OWNER/REPO",
  "--default-branch", $DEFAULT_BRANCH,
  "--require-auto-merge-workflows",
  "--expected-repository-id", $SELECTED_REPOSITORY_ID,
  "--enable-delete-branch-on-merge",
  "--squash-merge-commit-title", "PR_TITLE"
)
function Assert-MergePreflightSchema {
  param([Parameter(Mandatory)][object]$Verdict)

  if ($Verdict -isnot [pscustomobject]) {
    throw "Merge-settings preflight must return one JSON object; do not mutate."
  }
  Assert-SelectedRepositoryId -RepositoryId $Verdict.repository_id
  foreach ($field in @("inspection_complete", "administration_permission", "auto_merge_enabled", "auto_merge_workflows_eligible", "merge_queue_applies", "ruleset_status_checks_required")) {
    if ($Verdict.$field -isnot [bool]) {
      throw "Merge-settings preflight has an invalid Boolean $field; do not mutate."
    }
  }
  foreach ($field in @("classic_status_checks_required", "status_checks_required")) {
    if ($null -eq $Verdict.PSObject.Properties[$field] -or
        ($null -ne $Verdict.$field -and $Verdict.$field -isnot [bool])) {
      throw "Merge-settings preflight has invalid optional check evidence $field; do not mutate."
    }
  }
  foreach ($field in @("repository", "default_branch", "decision")) {
    if ($Verdict.$field -isnot [string] -or [string]::IsNullOrWhiteSpace($Verdict.$field)) {
      throw "Merge-settings preflight has an invalid $field; do not mutate."
    }
  }
  foreach ($field in @("current_merge_methods", "desired_merge_methods")) {
    if ($Verdict.$field -isnot [pscustomobject]) {
      throw "Merge-settings preflight has an invalid $field mapping; do not mutate."
    }
    foreach ($method in @("squash", "merge", "rebase")) {
      if ($Verdict.$field.$method -isnot [bool]) {
        throw "Merge-settings preflight has an invalid $field.$method; do not mutate."
      }
    }
  }
  foreach ($field in @("current_settings", "requested_settings")) {
    if ($Verdict.$field -isnot [pscustomobject] -or
        $Verdict.$field.delete_branch_on_merge -isnot [bool] -or
        $Verdict.$field.squash_merge_commit_title -isnot [string] -or
        $Verdict.$field.squash_merge_commit_title -cnotin @("PR_TITLE", "COMMIT_OR_PR_TITLE")) {
      throw "Merge-settings preflight did not inspect the requested $field; do not mutate."
    }
  }
  foreach ($field in @("required_merge_methods", "methods_to_disable")) {
    if ($Verdict.$field -isnot [Array] -or @($Verdict.$field).Count -gt 3 -or
        @($Verdict.$field | Where-Object {
          $_ -isnot [string] -or $_ -cnotin @("squash", "merge", "rebase")
        }).Count -gt 0 -or
        @($Verdict.$field | Select-Object -Unique).Count -ne @($Verdict.$field).Count) {
      throw "Merge-settings preflight has an invalid $field inventory; do not mutate."
    }
  }
}

$preflightOutput = python $mergeSettingsPreflight @preflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Merge-settings inspection is inconclusive; do not mutate. $($preflightOutput | Out-String)"
}
$mergeSettingsPreflightResult = ($preflightOutput | Out-String) | ConvertFrom-Json
Assert-MergePreflightSchema -Verdict $mergeSettingsPreflightResult
if ($mergeSettingsPreflightResult.inspection_complete -isnot [bool] -or
    -not $mergeSettingsPreflightResult.inspection_complete) {
  throw "Merge-settings inspection is incomplete; do not mutate."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  [string]$mergeSettingsPreflightResult.repository, "OWNER/REPO"
) -or [string]$mergeSettingsPreflightResult.default_branch -cne $DEFAULT_BRANCH) {
  throw "Merge-settings preflight result is not bound to OWNER/REPO and DEFAULT_BRANCH; do not mutate."
}
if ($mergeSettingsPreflightResult.requested_settings.delete_branch_on_merge -ne $true -or
    $mergeSettingsPreflightResult.requested_settings.squash_merge_commit_title -cne "PR_TITLE") {
  throw "Merge-settings preflight did not bind the requested branch-deletion and squash-title settings; do not mutate."
}
if ($mergeSettingsPreflightResult.decision -eq "require-explicit-merge-method-removal-confirmation") {
  $methods = @($mergeSettingsPreflightResult.methods_to_disable) -join ", "
  throw "Disabling enabled merge methods ($methods) needs separate user confirmation; do not mutate."
}
if ($mergeSettingsPreflightResult.decision -eq "require-status-checks-before-installing-auto-merge-workflows") {
  throw "No effective required status check gates auto-merge. Configure and verify branch policy before installing auto-merge workflows."
}
if ($mergeSettingsPreflightResult.decision -notin @(
  "may-configure-merge-settings", "skip-auto-merge-workflows",
  "enable-auto-merge-before-installing-workflows"
)) {
  throw "Merge-settings preflight returned an unknown decision; do not mutate."
}
$enableMergeCommit = [bool]$mergeSettingsPreflightResult.desired_merge_methods.merge
$enableRebaseMerge = [bool]$mergeSettingsPreflightResult.desired_merge_methods.rebase
$hasMergeQueue = [bool]$mergeSettingsPreflightResult.merge_queue_applies
$installAutoMergeWorkflows = [bool]$mergeSettingsPreflightResult.auto_merge_workflows_eligible
# Set only after the user explicitly requests one of the shipped assets and
# separately approves enabling this optional repository capability.
$installAutoMergeAssetsRequested = $false
$autoMergeCapabilityEnableApproved = $false
```

Sau khi duyệt riêng method cần bỏ, thêm `--confirm-disable-merge-methods`, chạy
lại preflight và yêu cầu decision lại là `may-configure-merge-settings`,
`skip-auto-merge-workflows` hoặc `enable-auto-merge-before-installing-workflows`.
Nếu decision cuối cần bật auto-merge hoặc required status check, không copy
auto-merge asset cho đến khi mutation riêng đã duyệt thành công, state cuối đã
xác minh và preflight chạy lại trả `may-configure-merge-settings`.

Chỉ dùng `$enableMergeCommit`, `$enableRebaseMerge` và
`$installAutoMergeWorkflows` từ JSON cuối. Chạy detailed schema check trên mọi
verdict ban đầu/cuối/per-setting/sau mutation trước cast hoặc policy comparison.
Giữ status-check evidence nullable tường minh khi producer chưa inspect classic
check hoặc merge queue làm auto-merge check tùy chọn không áp dụng; field thiếu
không tương đương null tường minh. Consumer trên yêu cầu branch deletion và
squash-title, nên phải inspect cả hai.
Effective-rule inspection chi tiết bên dưới giải thích field policy GitHub; không
thay helper result bằng giá trị tự suy luận. Repository-level auto-merge là
opt-in. Chỉ đặt `$installAutoMergeAssetsRequested = $true` sau khi user chọn một
trong hai asset. Nếu preflight báo
`enable-auto-merge-before-installing-workflows`, chỉ đặt
`$autoMergeCapabilityEnableApproved = $true` sau approval riêng để đổi setting
repository. Thiếu một trong hai điều kiện thì giữ state đã quan sát, không cài
asset nào.

Chỉ để giải thích/troubleshoot, inspection sau cho thấy effective-rule field mà
helper đánh giá. Built-in `GITHUB_TOKEN` không thêm PR vào merge queue; các
workflow đi kèm chủ ý không phải queue workflow:

```powershell
$repoViewOutput = gh repo view github.com/OWNER/REPO --json nameWithOwner,defaultBranchRef
if ($LASTEXITCODE -ne 0) { throw "Failed to read repository metadata." }
$repoView = $repoViewOutput | ConvertFrom-Json
$owner, $repo = $repoView.nameWithOwner -split '/', 2
$defaultBranch = $repoView.defaultBranchRef.name
if ([string]::IsNullOrWhiteSpace($defaultBranch)) { throw "No default branch to inspect." }
$encodedBranch = [Uri]::EscapeDataString($defaultBranch)

$effectiveRuleOutput = gh api --hostname github.com --paginate --slurp `
  "repos/$owner/$repo/rules/branches/${encodedBranch}?per_page=100" 2>&1
$effectiveRuleExitCode = $LASTEXITCODE
if ($effectiveRuleExitCode -ne 0) {
  throw "Could not inspect effective merge rules; do not change merge methods or install auto-merge workflows. $($effectiveRuleOutput | Out-String)"
}
try {
  $effectiveRulePages = (($effectiveRuleOutput | Out-String) | ConvertFrom-Json)
  $effectiveRules = @(
    foreach ($page in @($effectiveRulePages)) {
      foreach ($rule in @($page)) { $rule }
    }
  )
} catch {
  throw "Effective branch rules returned invalid JSON; do not change merge methods or install auto-merge workflows."
}

$supportedMergeMethods = @("merge", "squash", "rebase")
$requiredRepositoryMergeMethods = [Collections.Generic.HashSet[string]]::new(
  [StringComparer]::OrdinalIgnoreCase
)
$mergeQueueRules = @($effectiveRules | Where-Object { $_.type -eq "merge_queue" })
$pullRequestRules = @($effectiveRules | Where-Object { $_.type -eq "pull_request" })

foreach ($rule in $mergeQueueRules) {
  $mergeMethod = [string]$rule.parameters.merge_method
  if ([string]::IsNullOrWhiteSpace($mergeMethod) -or
      $supportedMergeMethods -notcontains $mergeMethod.ToLowerInvariant()) {
    throw "An effective merge queue has a missing or unsupported merge method; preserve repository merge settings."
  }
  [void]$requiredRepositoryMergeMethods.Add($mergeMethod.ToLowerInvariant())
}
foreach ($rule in $pullRequestRules) {
  $allowedMergeMethods = @($rule.parameters.allowed_merge_methods)
  if ($allowedMergeMethods.Count -eq 0 -or
      @($allowedMergeMethods | Where-Object {
        $_ -isnot [string] -or $supportedMergeMethods -notcontains $_.ToLowerInvariant()
      }).Count -gt 0) {
    throw "An effective pull-request rule has missing or unsupported allowed merge methods; preserve repository merge settings."
  }
  foreach ($mergeMethod in $allowedMergeMethods) {
    [void]$requiredRepositoryMergeMethods.Add($mergeMethod.ToLowerInvariant())
  }
}

$hasMergeQueue = $mergeQueueRules.Count -gt 0
if ($hasMergeQueue) {
  Write-Warning "An effective merge queue rule applies. Skip repo-scaffold auto-merge workflows; design queue automation with a confirmed PAT or GitHub App token instead."
}
```

Tiếp tục cấu hình repository merge setting bên dưới kể cả khi
`$hasMergeQueue` là true, nhưng giữ mọi merge method mà effective merge queue
dùng hoặc effective pull-request rule cho phép. Chỉ cài `auto-merge.yml` hoặc
`dependabot-auto-merge.yml` khi final preflight trả
`$installAutoMergeWorkflows` là true.

```powershell
# Values come only from the final merge-settings preflight. A queue configured
# for MERGE or REBASE must keep that method enabled or GitHub will block it.
$initialMergePlan = [ordered]@{
  repository = [string]$mergeSettingsPreflightResult.repository
  default_branch = [string]$mergeSettingsPreflightResult.default_branch
  decision = [string]$mergeSettingsPreflightResult.decision
  desired_merge_methods = $mergeSettingsPreflightResult.desired_merge_methods
  methods_to_disable = @($mergeSettingsPreflightResult.methods_to_disable)
  merge_queue_applies = [bool]$mergeSettingsPreflightResult.merge_queue_applies
  auto_merge_enabled = [bool]$mergeSettingsPreflightResult.auto_merge_enabled
  auto_merge_workflows_eligible = [bool]$mergeSettingsPreflightResult.auto_merge_workflows_eligible
  status_checks_required = $mergeSettingsPreflightResult.status_checks_required
  requested_settings = $mergeSettingsPreflightResult.requested_settings
  current_settings = $mergeSettingsPreflightResult.current_settings
} | ConvertTo-Json -Depth 6 -Compress
$finalPreflightOutput = python $mergeSettingsPreflight @preflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Final merge-settings inspection is inconclusive; do not mutate. $($finalPreflightOutput | Out-String)"
}
$finalMergePreflight = ($finalPreflightOutput | Out-String) | ConvertFrom-Json
Assert-MergePreflightSchema -Verdict $finalMergePreflight
if ($finalMergePreflight.inspection_complete -isnot [bool] -or
    -not $finalMergePreflight.inspection_complete -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
      [string]$finalMergePreflight.repository, "OWNER/REPO"
    ) -or [string]$finalMergePreflight.default_branch -cne $DEFAULT_BRANCH) {
  throw "Final merge-settings preflight is incomplete or bound to another target; do not mutate."
}
$finalMergePlan = [ordered]@{
  repository = [string]$finalMergePreflight.repository
  default_branch = [string]$finalMergePreflight.default_branch
  decision = [string]$finalMergePreflight.decision
  desired_merge_methods = $finalMergePreflight.desired_merge_methods
  methods_to_disable = @($finalMergePreflight.methods_to_disable)
  merge_queue_applies = [bool]$finalMergePreflight.merge_queue_applies
  auto_merge_enabled = [bool]$finalMergePreflight.auto_merge_enabled
  auto_merge_workflows_eligible = [bool]$finalMergePreflight.auto_merge_workflows_eligible
  status_checks_required = $finalMergePreflight.status_checks_required
  requested_settings = $finalMergePreflight.requested_settings
  current_settings = $finalMergePreflight.current_settings
} | ConvertTo-Json -Depth 6 -Compress
if ($initialMergePlan -cne $finalMergePlan -or
    $finalMergePreflight.decision -notin @(
      "may-configure-merge-settings", "skip-auto-merge-workflows",
      "enable-auto-merge-before-installing-workflows"
    )) {
  throw "Merge settings or effective rules changed since approval; review the fresh preflight before mutating."
}
$mergeSettingsPreflightResult = $finalMergePreflight
$enableMergeCommit = [bool]$finalMergePreflight.desired_merge_methods.merge
$enableRebaseMerge = [bool]$finalMergePreflight.desired_merge_methods.rebase
$hasMergeQueue = [bool]$finalMergePreflight.merge_queue_applies
$installAutoMergeWorkflows = [bool]$finalMergePreflight.auto_merge_workflows_eligible
if ($installAutoMergeAssetsRequested -isnot [bool] -or
    $autoMergeCapabilityEnableApproved -isnot [bool]) {
  throw "Auto-merge asset choice and capability approval must be Boolean values; do not infer consent."
}
$enableAutoMergeNow = -not [bool]$finalMergePreflight.auto_merge_enabled -and
  $installAutoMergeAssetsRequested -and $autoMergeCapabilityEnableApproved
$expectedAutoMergeEnabled = [bool]$finalMergePreflight.auto_merge_enabled -or $enableAutoMergeNow

function Get-ValidatedFreshMergePreflight {
  param(
    [Parameter(Mandatory)][bool]$ExpectedAutoMergeEnabled,
    [Parameter(Mandatory)][bool]$ExpectedDeleteBranchOnMerge,
    [Parameter(Mandatory)][string]$ExpectedSquashMergeCommitTitle,
    [switch]$RequireDesiredMergeMethods
  )

  $output = python $mergeSettingsPreflight @preflightArguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    throw "Fresh merge-settings preflight is inconclusive; do not mutate. $($output | Out-String)"
  }
  try {
    $result = ($output | Out-String) | ConvertFrom-Json
  } catch {
    throw "Fresh merge-settings preflight returned invalid JSON; do not mutate."
  }
  Assert-MergePreflightSchema -Verdict $result
  $policy = [ordered]@{
    required_merge_methods = @($result.required_merge_methods)
    desired_merge_methods = $result.desired_merge_methods
    merge_queue_applies = [bool]$result.merge_queue_applies
    ruleset_status_checks_required = $result.ruleset_status_checks_required
    classic_status_checks_required = $result.classic_status_checks_required
    status_checks_required = $result.status_checks_required
  } | ConvertTo-Json -Depth 6 -Compress
  $approvedPolicy = [ordered]@{
    required_merge_methods = @($finalMergePreflight.required_merge_methods)
    desired_merge_methods = $finalMergePreflight.desired_merge_methods
    merge_queue_applies = [bool]$finalMergePreflight.merge_queue_applies
    ruleset_status_checks_required = $finalMergePreflight.ruleset_status_checks_required
    classic_status_checks_required = $finalMergePreflight.classic_status_checks_required
    status_checks_required = $finalMergePreflight.status_checks_required
  } | ConvertTo-Json -Depth 6 -Compress
  if ($result.inspection_complete -isnot [bool] -or
      -not $result.inspection_complete -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$result.repository, "OWNER/REPO"
      ) -or [string]$result.default_branch -cne $DEFAULT_BRANCH -or
      $result.administration_permission -ne $true -or
      $result.requested_settings.delete_branch_on_merge -ne $true -or
      $result.requested_settings.squash_merge_commit_title -cne "PR_TITLE" -or
      [bool]$result.auto_merge_enabled -ne $ExpectedAutoMergeEnabled -or
      [bool]$result.current_settings.delete_branch_on_merge -ne $ExpectedDeleteBranchOnMerge -or
      [string]$result.current_settings.squash_merge_commit_title -cne $ExpectedSquashMergeCommitTitle -or
      $policy -cne $approvedPolicy -or
      $result.decision -notin @(
        "may-configure-merge-settings", "skip-auto-merge-workflows",
        "enable-auto-merge-before-installing-workflows"
      )) {
    throw "Fresh merge policy, repository settings, or request changed; review it again before mutating."
  }
  if ($RequireDesiredMergeMethods -and
      ([bool]$result.current_merge_methods.squash -ne [bool]$result.desired_merge_methods.squash -or
       [bool]$result.current_merge_methods.merge -ne [bool]$result.desired_merge_methods.merge -or
       [bool]$result.current_merge_methods.rebase -ne [bool]$result.desired_merge_methods.rebase)) {
    throw "Fresh merge-method state does not match the approved plan; do not continue mutating."
  }
  return $result
}

$mergeArguments = @(
  "repo", "edit", "github.com/OWNER/REPO",
  "--enable-squash-merge=true",
  "--enable-merge-commit=$($enableMergeCommit.ToString().ToLowerInvariant())",
  "--enable-rebase-merge=$($enableRebaseMerge.ToString().ToLowerInvariant())",
  "--delete-branch-on-merge"
)
$completedMergeUpdates = @()
$mergeSettingsFailure = $null
try {
  $mergeOutput = & gh @mergeArguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure merge methods. $($mergeOutput | Out-String)"
  }
  $completedMergeUpdates += "merge_methods_and_branch_cleanup"

  $null = Get-ValidatedFreshMergePreflight `
    -ExpectedAutoMergeEnabled ([bool]$finalMergePreflight.auto_merge_enabled) `
    -ExpectedDeleteBranchOnMerge $true `
    -ExpectedSquashMergeCommitTitle ([string]$finalMergePreflight.current_settings.squash_merge_commit_title) `
    -RequireDesiredMergeMethods
  $squashTitleOutput = & gh api --hostname github.com -X PATCH `
    repos/OWNER/REPO -f squash_merge_commit_title=PR_TITLE 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure the squash commit title. $($squashTitleOutput | Out-String)"
  }
  $completedMergeUpdates += "squash_merge_commit_title"

  if ($enableAutoMergeNow) {
    $null = Get-ValidatedFreshMergePreflight `
      -ExpectedAutoMergeEnabled $false `
      -ExpectedDeleteBranchOnMerge $true `
      -ExpectedSquashMergeCommitTitle "PR_TITLE" `
      -RequireDesiredMergeMethods
    $autoMergeOutput = & gh repo edit github.com/OWNER/REPO --enable-auto-merge 2>&1
    if ($LASTEXITCODE -ne 0) {
      $autoMergeError = $autoMergeOutput | Out-String
      if ($autoMergeError -match '(?is)HTTP 403') {
        throw "GitHub forbade auto-merge enablement. Verify repository plan and Administration permission. Do not install auto-merge workflows. $autoMergeError"
      }
      if ($autoMergeError -match '(?is)HTTP 404') {
        throw "The repository is missing or inaccessible. Re-verify repository identity; do not install auto-merge workflows. $autoMergeError"
      }
      if ($autoMergeError -match '(?is)HTTP 422') {
        throw "GitHub rejected auto-merge enablement as invalid, ineligible, or abuse-limited. Inspect the preserved response; do not install auto-merge workflows. $autoMergeError"
      }
      throw "Failed to enable auto-merge. $autoMergeError"
    }
    $completedMergeUpdates += "auto_merge"
  }
} catch {
  $mergeSettingsFailure = $_.Exception.Message
}

# These settings are changed by separate API operations. Re-read every requested
# field even after a failure so partial state is visible and cannot be reported as
# an all-or-nothing success.
$finalMergeOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
if ($LASTEXITCODE -ne 0) {
  if ($null -ne $mergeSettingsFailure) {
    throw "$mergeSettingsFailure The update sequence is non-atomic and final merge settings could not be read. Confirmed successful calls: $($completedMergeUpdates -join ', ')."
  }
  throw "Merge-setting mutations returned success, but the final state could not be verified. $($finalMergeOutput | Out-String)"
}
$finalMergeSettings = ($finalMergeOutput | Out-String) | ConvertFrom-Json
Assert-SelectedRepositoryId -RepositoryId $finalMergeSettings.id
if ($finalMergeSettings.full_name -isnot [string] -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
      $finalMergeSettings.full_name, "OWNER/REPO"
    )) {
  throw "Final merge-setting readback is not bound to OWNER/REPO; state remains unverified."
}
foreach ($setting in @("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge", "delete_branch_on_merge", "allow_auto_merge")) {
  if ($finalMergeSettings.$setting -isnot [bool]) {
    throw "Final merge-setting readback has an invalid Boolean $setting; state remains unverified."
  }
}
if ($finalMergeSettings.squash_merge_commit_title -isnot [string]) {
  throw "Final merge-setting readback has an invalid squash commit title policy; state remains unverified."
}
$finalMergeSummary = [ordered]@{
  allow_squash_merge = [bool]$finalMergeSettings.allow_squash_merge
  allow_merge_commit = [bool]$finalMergeSettings.allow_merge_commit
  allow_rebase_merge = [bool]$finalMergeSettings.allow_rebase_merge
  delete_branch_on_merge = [bool]$finalMergeSettings.delete_branch_on_merge
  allow_auto_merge = [bool]$finalMergeSettings.allow_auto_merge
  squash_merge_commit_title = [string]$finalMergeSettings.squash_merge_commit_title
} | ConvertTo-Json -Compress
if ($null -ne $mergeSettingsFailure) {
  throw "$mergeSettingsFailure The non-atomic sequence may be partially applied. Confirmed successful calls: $($completedMergeUpdates -join ', '). Final state: $finalMergeSummary"
}

$mergeSettingProblems = @()
if (-not [bool]$finalMergeSettings.allow_squash_merge) {
  $mergeSettingProblems += "squash merge is disabled"
}
if ([bool]$finalMergeSettings.allow_merge_commit -ne $enableMergeCommit) {
  $mergeSettingProblems += "merge-commit setting differs from effective-rule requirements"
}
if ([bool]$finalMergeSettings.allow_rebase_merge -ne $enableRebaseMerge) {
  $mergeSettingProblems += "rebase setting differs from effective-rule requirements"
}
if (-not [bool]$finalMergeSettings.delete_branch_on_merge) {
  $mergeSettingProblems += "head-branch deletion is disabled"
}
if ([bool]$finalMergeSettings.allow_auto_merge -ne $expectedAutoMergeEnabled) {
  $mergeSettingProblems += "auto-merge setting differs from the explicitly approved target"
}
if ([string]$finalMergeSettings.squash_merge_commit_title -cne "PR_TITLE") {
  $mergeSettingProblems += "squash commit title is not PR_TITLE"
}
if ($mergeSettingProblems.Count -gt 0) {
  throw "Repository merge settings did not reach the requested state: $($mergeSettingProblems -join '; '). Final state: $finalMergeSummary"
}
$postMergePreflightOutput = python $mergeSettingsPreflight @preflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Merge settings were updated, but the final auto-merge eligibility check is inconclusive; do not install either workflow. $($postMergePreflightOutput | Out-String)"
}
$postMergePreflight = ($postMergePreflightOutput | Out-String) | ConvertFrom-Json
Assert-MergePreflightSchema -Verdict $postMergePreflight
if ($postMergePreflight.inspection_complete -isnot [bool] -or
    -not $postMergePreflight.inspection_complete -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
      [string]$postMergePreflight.repository, "OWNER/REPO"
    ) -or [string]$postMergePreflight.default_branch -cne $DEFAULT_BRANCH -or
    [bool]$postMergePreflight.auto_merge_enabled -ne [bool]$finalMergeSettings.allow_auto_merge -or
    [bool]$finalMergeSettings.allow_squash_merge -ne [bool]$postMergePreflight.desired_merge_methods.squash -or
    [bool]$finalMergeSettings.allow_merge_commit -ne [bool]$postMergePreflight.desired_merge_methods.merge -or
    [bool]$finalMergeSettings.allow_rebase_merge -ne [bool]$postMergePreflight.desired_merge_methods.rebase -or
    [bool]$finalMergeSettings.delete_branch_on_merge -ne [bool]$postMergePreflight.current_settings.delete_branch_on_merge -or
    [string]$finalMergeSettings.squash_merge_commit_title -cne [string]$postMergePreflight.current_settings.squash_merge_commit_title) {
  throw "Merge settings or effective branch rules changed during configuration; review the fresh result and do not install either workflow."
}
$installAutoMergeWorkflows = [bool]$postMergePreflight.auto_merge_workflows_eligible
if ($installAutoMergeAssetsRequested -and -not $installAutoMergeWorkflows) {
  Write-Warning "Fresh merge-settings preflight returned '$($postMergePreflight.decision)'; do not install either auto-merge workflow."
}
```

Trước tiên chạy workflow-installation preflight chính xác cho asset đã chọn.
Ngay trước khi copy từng auto-merge asset, chạy lại merge preflight với cùng
setting đã duyệt và effective-rule evidence:

```powershell
if ($installAutoMergeAssetsRequested -isnot [bool] -or $installAutoMergeWorkflows -isnot [bool]) {
  throw "Auto-merge copy choice or eligibility is malformed; do not copy the asset."
}
if ($installAutoMergeAssetsRequested -and $installAutoMergeWorkflows) {
  $copyMergePreflight = Get-ValidatedFreshMergePreflight `
    -ExpectedAutoMergeEnabled $true `
    -ExpectedDeleteBranchOnMerge $true `
    -ExpectedSquashMergeCommitTitle "PR_TITLE" `
    -RequireDesiredMergeMethods
  if (-not $copyMergePreflight.auto_merge_workflows_eligible) {
    throw "Fresh merge-settings preflight no longer permits this asset; do not copy it."
  }
}
```

Nếu chọn cả hai asset, lặp lại check này trước asset thứ hai. Default branch,
effective queue/rule, required-check eligibility hoặc repository setting thay đổi
thì installation verdict cũ không cho phép copy tiếp.

Repository API setting `squash_merge_commit_title=PR_TITLE` khiến squash commit
cuối dùng PR title. Nếu cài `commitlint.yml`, job `commitlint` không cần
dependency sẽ validate title đó cùng PR commit, giữ Conventional Commit input
cho release-please. Auto-merge workflow cần `--enable-auto-merge` để
`gh pr merge --auto` chạy được, gồm Dependabot auto-merge và `auto-merge.yml` có
label gate.

## Token cho release-please (RELEASE_PLEASE_TOKEN)

Trước khi copy `release.yml`, `release-please.yml` hoặc release variant có
attestation, chạy bundled release preflight từ installed skill root. Script xác
minh đúng GitHub.com repository identity, từ chối repository archived/disabled,
bind installation với remote default branch và chọn attestation variant được
phép. Không suy eligibility attestation của private/internal repo chỉ từ
visibility: GitHub yêu cầu Enterprise Cloud, chỉ dùng
`--github-enterprise-cloud` sau khi xác minh riêng plan đó.

```bash
python "$REPO_SCAFFOLD_SKILL_ROOT/scripts/release_preflight.py" \
  --repository OWNER/REPO \
  --expected-repository-id "$SELECTED_REPOSITORY_ID" \
  --default-branch DEFAULT_BRANCH \
  --with-attestations
```

Chỉ dùng asset có attestation khi JSON decision là
`may-install-attestation-workflows`. Nếu trả `render-no-attestation-variant`,
dùng biến thể no-attestation đã tài liệu hóa. `inconclusive` cấm release
workflow mutation cho đến khi sửa evidence. Với private/internal GitHub
Enterprise Cloud đã xác nhận, thêm `--github-enterprise-cloud`; bỏ
`--with-attestations` nếu không yêu cầu provenance.

Trước khi copy asset release, cùng result phải báo `repository` bằng
`OWNER/REPO` (không phân biệt hoa thường) và `default_branch` trùng tuyệt đối
`DEFAULT_BRANCH`. Nếu target hoặc default branch đổi, chạy lại preflight ngay
trước copy. Typed `repository_id` phải bằng numeric `SELECTED_REPOSITORY_ID` dương
ban đầu; discovery chưa bind không cho phép asset attestation hoặc no-attestation.
Secret-name inspection và eligibility check nhiều request revalidate identity
cùng repository state điều khiển trước verdict cuối. Mọi drift là inconclusive,
không phải fallback no-attestation hợp lệ. Điều này không cấp quyền publish
release hoặc chứng minh secret có đúng scope.

Chỉ coi suffix cục bộ `+codex.<cachebuster>` của plugin-creator là installation
identity. Không đưa vào public release manifest, plugin version, changelog hoặc
tag; xác nhận và dùng SemVer công khai sạch. Chỉ giữ SemVer build metadata khác
khi user xác nhận rõ nó thuộc public release identity.

`release.yml` đi kèm hỗ trợ đường recovery thủ công đã xác minh, không cần
`push.tags` trigger. Chỉ chạy sau khi exact tag tồn tại và resolve đến full
commit SHA đã truyền. Ba privileged release job chỉ nhận ba caller route:
`workflow_dispatch` trực tiếp `release.yml` từ default branch;
`release-please.yml` gọi trên default-branch push; hoặc `release-tag.yml` gọi cho
`v*` tag vừa push với tag/commit input khớp. Workflow-call file, event và ref
khác bị chặn trước khi cấp build, OIDC hoặc publication permission. GitHub gắn
`github` context reusable workflow với caller, nên gate bind event/ref/caller
file (xem [reusable workflow docs](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)).
Với release theo tag, workflow definition vẫn lấy từ tag ref; chỉ tag creator
đáng tin được dùng đường này. Xem [workflow ref semantics](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows)
và [push-event SHA definition](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

```bash
gh workflow run release.yml --repo OWNER/REPO --ref DEFAULT_BRANCH \
  -f tag=vX.Y.Z -f commit_sha=FULL_COMMIT_SHA
```

Chỉ cần token nếu repo phát hành `release-please.yml` (hoặc `auto-merge.yml`,
workflow ưu tiên token này). Dùng fine-grained PAT để PR do release-please tạo
có thể chạy required check và merge qua workflow có label gate có thể kích hoạt
release-please run trên default branch. Event do `GITHUB_TOKEN` mặc định tạo
không khởi chạy workflow run khác.

`auto-merge.yml` chỉ được đọc PAT này từ `pull_request_target` workflow trên base
đáng tin sau khi human-user và same-repository guard đạt. Không đổi workflow
sang `pull_request` khi còn tham chiếu PAT; không thêm checkout, fetch, artifact
download hoặc command chạy nội dung do PR kiểm soát.

Giữ nguyên các giá trị locale Release Please chính xác sau để scaffold lặp lại
có kết quả xác định:

| Locale | Tiêu đề pull request | Header | Phần mở đầu footer |
| --- | --- | --- | --- |
| `en` | `chore${scope}: release${component} ${version}` | `:robot: I have created a release *beep* *boop*` | `This PR was generated with` |
| `vi` | `chore${scope}: phát hành${component} ${version}` | `:robot: Release Please đã tạo PR phát hành tự động này.` | `PR này được tạo tự động bằng` |

Với `en`, dùng `Features`, `Bug Fixes`, `Performance Improvements`, `Reverts`,
`Documentation`, `Styles`, `Miscellaneous Chores`, `Code Refactoring`, `Tests`,
`Build System`, `Continuous Integration`. Với `vi`, dùng `Tính năng`, `Sửa lỗi`,
`Cải thiện hiệu năng`, `Hoàn tác`, `Tài liệu`, `Định dạng mã`, `Bảo trì khác`,
`Tái cấu trúc mã`, `Kiểm thử`, `Hệ thống xây dựng`, `Tích hợp liên tục`. Giữ các
entry `feat` và `feature` lặp lại cùng mọi `hidden` flag của asset. Hoàn thiện
footer bằng các link Release Please/tài liệu giống English asset; chỉ dịch prose
xung quanh.

Khi cài release-please, copy assets/release-please-config.json và
assets/release-please-manifest.json vào root repository lần lượt thành
release-please-config.json và .release-please-manifest.json. Render PR title,
header, footer và tên changelog section theo SCAFFOLD_LANGUAGE đã resolve. Giữ
nguyên placeholder scope, component, version trong title pattern, cùng thứ tự
changelog type và hidden flag của asset để localization không đổi release
semantics.

Trước khi đổi title pattern trong setup có sẵn, liệt kê release PR đang mở và
phối hợp chuyển đổi: release-please dùng pattern đã cấu hình để tạo/phân tích
title, vì vậy đổi release PR hiện tại sang đúng pattern mới ngay trước khi config
vào nhánh, hoặc đợi PR đó được giải quyết. Sau lần chạy đầu với config mới, xác
minh PR release gốc đã được cập nhật và không có PR trùng được mở.

Config cố ý kết hợp draft: true với force-tag-creation: true. release-please tạo
tag trước draft Release; không ghép mode này với release-tag.yml hoặc caller
push.tags: v* khác, vì caller có thể thấy tag trước khi draft tồn tại. Thay vào
đó, release-please.yml đã phát hành đợi action hoàn tất rồi gọi reusable release.yml
bằng tag được tạo và output sha của action. Engine tuần tự hóa caller theo tag.

Build job read-only xác minh tag qua Git database references/tags REST API đã
xác thực, checkout commit immutable không giữ credential, build/xác minh artifact
regular file rồi chuyển bằng artifact action đã pin SHA. Nếu repository đủ điều
kiện, attestation job mới tải file không checkout, xác minh mà không chạy project
code và tạo SLSA build provenance bằng actions/attest; chỉ job này nhận
id-token: write và attestations: write, reusable workflow caller truyền hai
permission đó xuống. GitHub hiện hỗ trợ attestation cho repository công khai
trên plan hiện tại và private/internal trên GitHub Enterprise Cloud. Với repo
không đủ điều kiện, render no-attestation variant đã tài liệu hóa, không để gate
chắc chắn thất bại.

Publish job riêng có quyền write, chạy trên runner mới, tải artifact nhưng
không thực thi, đợi attestation khi bật, xác minh tag ngay trước publish và kiểm
tra lại sau publish. Khi tạo draft Release còn thiếu, --verify-tag ngăn GitHub
CLI âm thầm tạo lại tag đã mất sau xác minh. Tag mismatch trước publish thì giữ
Release ở draft và fail. Mismatch sau publish là integrity incident: workflow
báo nhưng không rollback được. Dùng ruleset tag hiệu lực hoặc immutable release
khi cần ngăn tag di chuyển thay vì chỉ phát hiện.

Rerun chỉ được sửa draft: bỏ qua asset hiện có khi name, size, SHA-256 digest và
state khớp chính xác artifact dự kiến; chỉ upload asset thiếu, không thay asset
đã có. Asset bất ngờ hoặc xung đột làm run dừng. Publisher từ chối mọi Release
đã publish, kể cả release mutable cũ; hãy tạo version tag mới. Điền manifest
bằng version hiện tại đã xác nhận, không có tiền tố v; không tự bịa version ban
đầu. Khi release.yml chịu trách nhiệm artifact, không bỏ option nào hoặc gộp
ranh giới permission giữa build, attestation đủ điều kiện và publish.

Release publisher giới hạn release list 16 trang, đọc lại trạng thái draft của
tag trước mỗi lần upload/publish, so sánh đầy đủ asset đã upload theo name, size,
SHA-256 digest với build output và xác minh state published. Khi rerun draft, chỉ
skip asset khớp hoàn toàn; chỉ upload asset còn thiếu, không thay asset hiện có.
Asset lạ/xung đột làm run dừng. History quá lớn hoặc state mismatch đều fail
closed.

GitHub [REST guidance về conditional request](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
nêu unsafe request không conditional trừ khi endpoint tài liệu hóa hỗ trợ.
[Release asset upload endpoint](https://docs.github.com/en/rest/releases/assets)
chỉ mô tả request upload riêng, không có draft-state precondition. Suy ra luồng
này không thể bind atomic lần đọc draft với upload/publish write. Nó kiểm tra lại
ngay trước mỗi request và không thay asset cũ, nhưng publisher bên ngoài vẫn có
thể race trong cửa sổ đó; giới hạn quyền release write và bật immutable release
khi được hỗ trợ.

Freshness registry đi kèm coi release-please-config.json là tùy chọn: bỏ qua path
cho đến khi cài Release Please, sau đó audit $schema ở lần freshness run theo
lịch/thủ công kế tiếp. Không xóa tracker entry đó. Workflow tương tự có
CI-toolchain policy và checker đi kèm, nên Markdown tooling pin cũng được
reminder theo dõi.

Sau khi tải artifact đã publish, xác minh cả provenance lẫn reusable signer
workflow:

```bash
gh attestation verify PATH/TO/ARTIFACT \
  --repo OWNER/REPO \
  --signer-workflow OWNER/REPO/.github/workflows/release.yml
```

1. Tạo PAT (GitHub UI → Settings → Developer settings → Fine-grained tokens,
   hoặc `gh` nếu có). Cấp quyền tối thiểu: **chỉ repository này**, permission
   **Contents: Read and write** cùng **Pull requests: Read and write** (thêm
   **Issues: Read and write** nếu release-please quản lý issue). Không cấp gì
   khác.
2. Thêm nó thành repository secret có tên **chính xác** `RELEASE_PLEASE_TOKEN`
   (Settings → Secrets and variables → Actions → New repository secret). Tên
   phải khớp từng ký tự với tham chiếu `secrets.RELEASE_PLEASE_TOKEN` trong
   workflow. Tên secret chỉ nhận chữ cái, số và dấu gạch dưới (không có gạch
   nối/khoảng trắng); sai tên khiến action báo `Input required: token`.

3. Trước khi cài `release-please.yml` hoặc `auto-merge.yml` phụ thuộc PAT, chỉ
   xác minh tên secret chính xác ở phạm vi repository. Không lấy, in hoặc tiết
   lộ giá trị secret. Secret API không khả dụng hoặc tên thiếu/không khớp là
   inconclusive, cấm workflow mutation:

   ```bash
   python "$REPO_SCAFFOLD_SKILL_ROOT/scripts/release_preflight.py" \
     --repository OWNER/REPO \
     --expected-repository-id "$SELECTED_REPOSITORY_ID" \
     --default-branch DEFAULT_BRANCH \
     --require-release-please-token
   ```

   Chỉ tiếp tục khi `release_please_token` là `verified-present` và JSON result
   báo đúng input `OWNER/REPO` cùng `DEFAULT_BRANCH`. Caller vẫn phải xác nhận
   PAT có đúng scope tối thiểu đã nêu; GitHub secret API chủ ý không chứng minh
   được scope đó.

Không dán giá trị token vào chat, commit hoặc log. Nếu bị lộ, thu hồi ngay và
tạo token mới.

## Xác minh

Trước hết đọc visibility, fork state và default branch:

```bash
gh repo view github.com/OWNER/REPO --json visibility,isFork,defaultBranchRef
```

- Với repository không phải fork, xác minh community profile health. Resource
  công khai query được không cần xác thực; private repository cần token đã xác
  thực có `Contents: read`:

  ```bash
  gh api --hostname github.com repos/OWNER/REPO/community/profile --jq '.health_percentage'
  ```

- Chỉ bỏ community-profile call khi target là fork. Với private non-fork, chạy
  bằng `gh` đã xác thực; nếu lỗi, báo permission/API error, không tuyên bố private
  repository không được hỗ trợ.
- Xác nhận license detection:

  ```bash
  gh api --hostname github.com repos/OWNER/REPO/license --jq '.license.spdx_id'
  ```

- Xác minh protection mechanism đã chọn trên default branch được phát hiện.
  URL-encode branch vì tên branch hợp lệ có thể chứa `/`:

  ```powershell
  $repoViewOutput = gh repo view github.com/OWNER/REPO --json nameWithOwner,defaultBranchRef
  if ($LASTEXITCODE -ne 0) { throw "Failed to read repository metadata." }
  $repoView = $repoViewOutput | ConvertFrom-Json
  $owner, $repo = $repoView.nameWithOwner -split '/', 2
  $defaultBranch = $repoView.defaultBranchRef.name
  if ([string]::IsNullOrWhiteSpace($defaultBranch)) { throw "No default branch to verify." }
  $encodedBranch = [Uri]::EscapeDataString($defaultBranch)

  # Classic branch protection, when configured.
  gh api --hostname github.com "repos/$owner/$repo/branches/$encodedBranch/protection" `
    --jq '{pr: (.required_pull_request_reviews != null), admins: .enforce_admins.enabled, checks: ((.required_status_checks.checks // []) | map({context, app_id}))}'

  # Inspect effective active rulesets, including organization-level rulesets.
  gh api --hostname github.com --paginate "repos/$owner/$repo/rules/branches/$encodedBranch" `
    --jq '.[] | select(.type == "pull_request" or .type == "required_status_checks")'
  ```

Chạy classic command nếu plugin đã cấu hình classic protection. Luôn chạy
effective-rules command để nhận diện ruleset repository/tổ chức cần giữ; đây là
inspection, không chứng minh plugin đã cấu hình ruleset. Với classic protection,
so mọi check `context` và `app_id` trả về với đúng `$requiredCheckNames` cùng
`$requiredAppIdsByContext` đã dùng lúc setup. Với ruleset
`required_status_checks`, kiểm tra riêng `context` và `integration_id`, báo xung
đột với check dự kiến; không so ruleset field với classic `app_id`.

Query lại cả Check Runs và Commit Statuses trên head/test-merge SHA hiện tại của
mỗi PR đại diện, SHA default branch gần đây và merge-group SHA nếu áp dụng.
Test-merge SHA điều khiển nếu có bất kỳ Check Run/Commit Status nào; nếu không,
head SHA điều khiển. Xác minh mỗi required context chỉ có đúng một effective
job name và App ID controlling SHA khớp source đã cấu hình. Verification fail
nếu có Commit Status cùng tên trên SHA điều khiển nào. Xóa, đổi tên hoặc sửa
classic context mà workflow không phát, nhiều workflow cùng phát, app binding
khác hoặc xung đột giữa hai status system. Giữ ruleset có sẵn trừ khi người dùng
bắt đầu thay đổi ruleset policy riêng đã được duyệt.
