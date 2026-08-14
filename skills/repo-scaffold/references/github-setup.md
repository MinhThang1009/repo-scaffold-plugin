# GitHub Configuration Reference

Exact `gh` commands for step 5 (configure GitHub). Confirm each outward-facing action before running it.

This reference supports GitHub.com only. Before using any command, verify the remote host is exactly `github.com`, record its canonical `OWNER/REPO`, and substitute `github.com/OWNER/REPO` for repository CLI commands plus `--hostname github.com` for API commands. Stop remote configuration for GitHub Enterprise Server or GHE.com; the bundled workflows and commands are not a portable enterprise-host setup. Do not rely on `GH_HOST`, the current directory, or gh's default repository.

NOTE (Windows/Git-Bash): `gh api` paths must NOT start with a leading slash, or the shell rewrites them to a filesystem path (`C:/Program Files/Git/...`). Use `gh api --hostname github.com repos/OWNER/REPO/...`, never `gh api --hostname github.com /repos/...`.

## Contents

- [Repository identity preflight](#repository-identity-preflight)
- [Description and topics](#description-and-topics)
- [Repository communication features](#repository-communication-features)
- [Inherited community-health policy](#inherited-community-health-policy)
- [Branch protection (classic)](#branch-protection-classic)
- [Ruleset compatibility (inspect only)](#ruleset-compatibility-inspect-only)
- [Labels](#labels)
- [Dependabot](#dependabot)
- [Security features](#security-features)
- [Merge settings](#merge-settings)
- [release-please token](#release-please-token-release_please_token)
- [Verify](#verify)

## Repository identity preflight

Resolve the target before the first GitHub query. An argument-free `gh repo view` can follow gh's configured default repository; for a fork cloned with [`gh repo clone`](https://cli.github.com/manual/gh_repo_clone), GitHub CLI sets the parent repository as that default unless `--no-upstream` is used. Treat `gh repo set-default --view` as diagnostic output only.

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

When no GitHub.com candidate exists, stop remote discovery and continue as local-only. When one exists, use it. When several exist, display the remote mapping and ask the user to choose; never prefer `origin`, `upstream`, or gh's default. After selection, query only the explicit repository and cross-check the response:

```powershell
$selectedRepository = "OWNER/REPO" # selected from the verified remote candidates
$repoViewOutput = gh repo view "github.com/$selectedRepository" `
  --json nameWithOwner,url,owner,defaultBranchRef,visibility,isFork,isArchived 2>&1
if ($LASTEXITCODE -ne 0) { throw "Failed to read the selected GitHub repository." }
$repoView = ($repoViewOutput | Out-String) | ConvertFrom-Json
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
```

Pass `github.com/OWNER/REPO` to every later repository-scoped `gh` command. Never fall back to an argument-free query after this preflight.

## Description and topics

```powershell
# Keep user/repository text as data. Do not generate a command string and invoke it.
$description = Read-Host "One-line repository description"
if ([string]::IsNullOrWhiteSpace($description)) {
  throw "Repository description must be non-empty."
}
$topics = @("topic1", "topic2") # confirmed GitHub topic slugs
$topicArgs = @()
foreach ($topic in $topics) { $topicArgs += @('--add-topic', $topic) }
$editOutput = & gh repo edit github.com/OWNER/REPO --description $description @topicArgs 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Failed to update the repository description/topics. $($editOutput | Out-String)"
}

$metadataOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "The mutation returned success, but the repository metadata could not be verified. $($metadataOutput | Out-String)"
}
$metadata = ($metadataOutput | Out-String) | ConvertFrom-Json
$actualTopics = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($topic in @($metadata.topics)) { [void]$actualTopics.Add([string]$topic) }
$missingTopics = @($topics | Where-Object { -not $actualTopics.Contains($_) })
if ($metadata.description -cne $description -or $missingTopics.Count -gt 0) {
  throw "Repository metadata did not reach the requested state. Missing topics: $($missingTopics -join ', ')."
}
```

A non-empty description is required for a 100% community profile. Topics aid discovery.

## Repository communication features

Inspect these settings before generating issue templates or communication links:

```bash
gh repo view github.com/OWNER/REPO --json isArchived,hasIssuesEnabled,hasDiscussionsEnabled
```

Archived repositories are read-only; do not attempt remote configuration for them. When an active repository has Issues or Discussions disabled, ask before enabling either feature. Run approved mutations separately, then re-query the state before rendering dependent files:

```powershell
# Set these only from explicit user confirmation.
$enableIssuesRequested = $false
$enableDiscussionsRequested = $false

if ($enableIssuesRequested) {
  $issuesOutput = & gh repo edit github.com/OWNER/REPO --enable-issues 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Issues could not be enabled; issue-dependent output will be omitted. $($issuesOutput | Out-String)"
  }
}
if ($enableDiscussionsRequested) {
  $discussionsOutput = & gh repo edit github.com/OWNER/REPO --enable-discussions 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Discussions could not be enabled; discussion-dependent output will be omitted. $($discussionsOutput | Out-String)"
  }
}

$featureStateOutput = gh repo view github.com/OWNER/REPO `
  --json isArchived,hasIssuesEnabled,hasDiscussionsEnabled
if ($LASTEXITCODE -ne 0) {
  throw "Could not verify repository communication features; omit dependent output."
}
$featureState = $featureStateOutput | ConvertFrom-Json
if ($featureState.isArchived) { throw "Repository is archived; skip remote configuration." }
$hasIssuesEnabled = [bool]$featureState.hasIssuesEnabled
$hasDiscussionsEnabled = [bool]$featureState.hasDiscussionsEnabled
```

Use only `$hasIssuesEnabled` and `$hasDiscussionsEnabled` from that final query when rendering templates and links. If a feature remains disabled, omit its dependent output instead of shipping dead navigation. For a local-only repository, use confirmed non-GitHub contacts until a remote exists; intended future state is not an enabled capability.

## Inherited community-health policy

Before proposing local community files, inspect defaults inherited from the account's **public** `.github` repository. GitHub.com does not apply account defaults from an internal or private `.github` repository. Local files override defaults, and any local `.github/ISSUE_TEMPLATE` file disables the inherited issue-template directory as a set.

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

Compare the effective profile URLs with the target repository and `$owner/.github`, and report `$inheritedDefaultFiles`, `$inheritedIssueTemplates`, `$inheritedPullRequestTemplates`, and `$inheritedDiscussionForms` separately from local sources. Treat `$availableDefaultIssueTemplates` and `$availableDefaultPullRequestTemplates` as account defaults only, not effective inheritance: when the corresponding local template list is non-empty, report why those account defaults are suppressed and evaluate the local source by itself. If a target file is absent locally but effectively inherited, ask before creating the local override; approval to scaffold files generally is not approval to replace an account-wide policy.

When `$inheritedIssueTemplates` is non-empty, fetch each effective template from `$owner/.github` and parse its metadata before configuring labels. For issue forms, parse the YAML `labels` field; for legacy Markdown templates, parse only the YAML front matter and normalize its `labels` value. Do not scrape labels from template body text. Record the resulting label names as `$effectiveInheritedIssueLabels`. A referenced label works only when it exists in both `$owner/.github` and the target repository. Report an account-default label missing from `$owner/.github` as a broken default; do not mutate that separate repository without explicit confirmation. For a label present in `$owner/.github` but absent from the target, copy its confirmed name, color, and description to the target after confirmation. Never invent label metadata or claim the inherited template is fully functional until both repositories have been verified.

## Branch protection (classic)

Enable only when the user wants to enforce the PR flow. Require a PR, passing selected checks, up-to-date branches, and apply to admins too. A solo owner can still self-merge (0 required approvals). Use the detected default branch; never substitute a fixed branch name.

On GitHub.com, protected branches are available for private repositories only with GitHub Pro, Team, or Enterprise Cloud. For a private/internal repository, confirm entitlement before mutation when possible. Otherwise treat `403` as forbidden and preserve `404` as a missing-or-inaccessible repository/branch result unless separate evidence proves a plan limitation. Continue the scaffold without claiming protection was enabled.

Build the check list from contexts verified during the scaffold run, not from workflow filenames:

- Add `ci-success` only when the repo-scaffold CI asset was created or an existing CI workflow was approved for update and verified to emit that aggregate check.
- Add `dependency-review` only when the matching repo-scaffold asset was created for an eligible repository.
- Add `commitlint` only when the matching repo-scaffold workflow asset was created.
- For an unchanged existing workflow, inspect its `jobs` mapping and optional job-level `name`, then ask the user which real aggregate context to require. A matching filename is not evidence that a particular check exists.
- Compute the producers of every candidate required context across the final workflow set before mutation: use job-level `name` when present, otherwise the job ID, and resolve matrix/reusable-workflow names to the check context GitHub actually emits. GitHub does not scope required checks by workflow, matrix, or event, so each required context must have exactly one producer.
- Verify event coverage, not only the context name. A required producer must run for every `pull_request` without workflow-level `paths`, `paths-ignore`, or branch filters that can suppress the entire workflow. When an effective merge queue applies, it must also run for `merge_group` with `checks_requested`. Any job-level `if` must evaluate true and execute the real validation for every relevant event; GitHub reports skipped jobs as successful, which is not evidence that the gate ran. Duplicate names unrelated to the required set must not block protection.
- For a generated repo-scaffold asset, parse the final YAML and record this coverage directly. For an unchanged, dynamic, matrix-named, reusable, or externally supplied check, verify a representative PR run (and a merge-group run when applicable) or do not require it.
- For every representative PR, retrieve both `.head.sha` and the current `.merge_commit_sha` with `gh api --hostname github.com repos/OWNER/REPO/pulls/NUMBER --jq '{head_sha: .head.sha, test_merge_sha: .merge_commit_sha}'`. Require a mergeable representative PR with a non-null test-merge SHA. Inspect complete paginated results from Check Runs (`gh api --hostname github.com --paginate repos/OWNER/REPO/commits/SHA/check-runs`) and Commit Statuses (`gh api --hostname github.com --paginate repos/OWNER/REPO/commits/SHA/statuses`) on both SHAs, plus a merge-group SHA when applicable and recent default-branch SHAs. When the test-merge commit has statuses, apply GitHub's documented precedence and treat it as controlling; never infer that head-only evidence is complete. A check must have completed successfully in this repository during the past seven days before it can be selected as required. Compare context names case-insensitively. GitHub requires both systems when a Check Run and Commit Status share a required name, so reject that candidate on any controlling SHA instead of treating it as one producer. Record the intended Check Run's exact positive `app.id`; reject an unknown, absent, or changing source. Bind every new required check to that verified app ID rather than allowing GitHub to auto-select a recent source.
- Stop before applying required-status-check protection when no real gate has been confirmed. Never submit a context that no workflow emits.

PowerShell example:

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
$encodedBranch = [Uri]::EscapeDataString($defaultBranch)

# Required checks need merge-group coverage whenever an effective queue rule applies.
$effectiveRuleOutput = gh api --hostname github.com --paginate `
  "repos/$owner/$repo/rules/branches/$encodedBranch" `
  --jq '.[] | select(.type == "merge_queue") | .type' 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Could not determine whether a merge queue applies; stop before configuring required checks. $($effectiveRuleOutput | Out-String)"
}
$hasMergeQueue = @($effectiveRuleOutput | Where-Object { $_ -eq "merge_queue" }).Count -gt 0

# Start false; set true only for files this scaffold run actually created or updated.
$installedRepoScaffoldCi = $false
$installedDependencyReview = $false
$installedCommitlint = $false

# REQUIRED INPUT: populate one object per effective job producer found by inspecting
# every final workflow. ProducerId is the stable workflow-path + job-id identity;
# duplicate Context values from different ProducerIds remain visible as ambiguity.
# Populate SourceVerified, HasCommitStatusCollision, and AppId from the API evidence
# described above; do not infer them from a workflow filename.
$effectiveWorkflowChecks = @(
  # [pscustomobject]@{
  #   Context = "ci-success"
  #   ProducerId = ".github/workflows/ci.yml#ci-success"
  #   PullRequestCoverage = $true
  #   MergeGroupCoverage = $true
  #   CanSkipRelevantEvents = $false
  #   SourceVerified = $true
  #   HasCommitStatusCollision = $false
  #   AppId = [int64]$verifiedAppId
  # }
)
if ($effectiveWorkflowChecks.Count -eq 0) {
  throw "No effective workflow check producers were inspected; stop before configuring protection."
}

$requiredCheckNames = @()
if ($installedRepoScaffoldCi) { $requiredCheckNames += "ci-success" }
if ($installedDependencyReview) { $requiredCheckNames += "dependency-review" }
if ($installedCommitlint) { $requiredCheckNames += "commitlint" }

# For an unchanged existing workflow, append only a context confirmed from its real job:
# $requiredCheckNames += "existing-ci-gate"

$duplicateRequiredNames = @(
  $requiredCheckNames | Group-Object | Where-Object Count -gt 1 |
    Select-Object -ExpandProperty Name
)
if ($duplicateRequiredNames.Count -gt 0) {
  throw "Duplicate required check names: $($duplicateRequiredNames -join ', '). Resolve the workflow job-name collision before configuring protection."
}
$requiredCheckNames = @($requiredCheckNames | Sort-Object)
if ($requiredCheckNames.Count -eq 0) {
  throw "No verified required check context; inspect the existing workflows before protecting the branch."
}

$producerProblems = @()
$requiredAppIdsByContext = [System.Collections.Generic.Dictionary[string, int64]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($context in $requiredCheckNames) {
  $producers = @($effectiveWorkflowChecks | Where-Object {
    [System.StringComparer]::OrdinalIgnoreCase.Equals($_.Context, $context)
  })
  $producerCount = @($producers.ProducerId | Sort-Object -Unique).Count
  if ($producerCount -ne 1) {
    $producerProblems += "${context}=${producerCount} producers"
    continue
  }
  $producer = $producers[0]
  if (-not $producer.PullRequestCoverage -or $producer.CanSkipRelevantEvents) {
    $producerProblems += "${context}=missing unconditional pull_request coverage"
  }
  if ($hasMergeQueue -and -not $producer.MergeGroupCoverage) {
    $producerProblems += "${context}=missing merge_group coverage"
  }
  if (-not $producer.SourceVerified) {
    $producerProblems += "${context}=unverified check source"
  }
  if ($producer.HasCommitStatusCollision) {
    $producerProblems += "${context}=Check Run/Commit Status collision"
  }
  $appId = 0L
  if ($null -eq $producer.AppId -or -not [int64]::TryParse(
    [string]$producer.AppId,
    [ref]$appId
  ) -or $appId -le 0) {
    $producerProblems += "${context}=missing positive GitHub App ID"
  } else {
    $requiredAppIdsByContext[$context] = $appId
  }
}
if ($producerProblems.Count -gt 0) {
  throw "Every required context must have one event-compatible producer across the final workflow set: $($producerProblems -join ', ')."
}

function Assert-ClassicProtectionState(
  [object]$Protection,
  [Collections.Generic.IDictionary[string, int64]]$ExpectedAppIdsByContext
) {
  $problems = @()
  if (-not [bool]$Protection.required_status_checks.strict) {
    $problems += "strict status checks are disabled"
  }
  if (-not [bool]$Protection.enforce_admins.enabled) {
    $problems += "administrator enforcement is disabled"
  }
  if ($null -eq $Protection.required_pull_request_reviews) {
    $problems += "pull request protection is absent"
  }

  $actualAppIdsByContext = [Collections.Specialized.OrderedDictionary]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($check in @($Protection.required_status_checks.checks)) {
    if ($null -eq $check -or [string]::IsNullOrWhiteSpace($check.context)) {
      continue
    }
    $appId = if ($null -eq $check.app_id) { -1L } else { [int64]$check.app_id }
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
$protectionOutput = gh api --hostname github.com $protectionPath -H "Accept: application/vnd.github+json" 2>&1
$protectionExitCode = $LASTEXITCODE

if ($protectionExitCode -eq 0) {
  $existingProtection = $protectionOutput | ConvertFrom-Json
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
    $entry.app_id = if ($null -eq $check.app_id) { -1 } else { [int64]$check.app_id }
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

  $statusPayload = @{
    strict = $true
    checks = @($checksByContext.Values)
  } | ConvertTo-Json -Depth 6
  $completedProtectionUpdates = @()
  $protectionUpdateFailure = $null
  try {
    $statusUpdateOutput = $statusPayload | gh api --hostname github.com -X PATCH `
      "$protectionPath/required_status_checks" `
      -H "Accept: application/vnd.github+json" --input - 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to update required status checks. $($statusUpdateOutput | Out-String)"
    }
    $completedProtectionUpdates += "required_status_checks"

    $adminUpdateOutput = gh api --hostname github.com -X POST `
      "$protectionPath/enforce_admins" -H "Accept: application/vnd.github+json" `
      --silent 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to enable admin enforcement. $($adminUpdateOutput | Out-String)"
    }
    $completedProtectionUpdates += "enforce_admins"

    # Enable the PR requirement only when it is absent. Never weaken an existing review policy.
    if ($null -eq $existingProtection.required_pull_request_reviews) {
      $reviewUpdateOutput = '{"required_approving_review_count":0}' | `
        gh api --hostname github.com -X PATCH `
          "$protectionPath/required_pull_request_reviews" `
          -H "Accept: application/vnd.github+json" --input - 2>&1
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to enable pull request review protection. $($reviewUpdateOutput | Out-String)"
      }
      $completedProtectionUpdates += "required_pull_request_reviews"
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
  Assert-ClassicProtectionState $finalProtection $expectedFinalAppIds
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
  Assert-ClassicProtectionState $createdProtection $requiredAppIdsByContext
} elseif (($protectionOutput | Out-String) -match '(?is)HTTP 403') {
  throw "GitHub forbade branch-protection inspection. Verify repository plan and Administration permission; no settings were changed. $($protectionOutput | Out-String)"
} elseif (($protectionOutput | Out-String) -match '(?is)HTTP 404') {
  throw "The repository or detected default branch was not found or is inaccessible. Re-verify repository identity and DEFAULT_BRANCH; no settings were changed. $($protectionOutput | Out-String)"
} else {
  throw "Could not read existing branch protection; no settings were changed. $($protectionOutput | Out-String)"
}
```

`ci-success` is the aggregate test gate shipped in `assets/workflows/ci.yml`: it is green only if every `test` matrix job passed. Requiring it instead of every matrix combination keeps the matrix-specific list stable. Dependency review and the dependency-free Conventional Commit gate (`commitlint`) are independent jobs, so they must be required separately when their repo-scaffold assets were installed. All three shipped required-check workflows include unfiltered `pull_request` and `merge_group` coverage. Existing workflows may use different job IDs, names, triggers, or filters; preserve those files and require only contexts verified from their actual definitions and event coverage.

## Ruleset compatibility (inspect only)

Rulesets can coexist with classic branch protection, and the most restrictive applicable rule wins. This plugin configures only classic branch protection because safely creating or editing a ruleset requires preserving repository and organization policy, bypass actors, target conditions, and rule-specific parameters. Do not call `POST`/`PUT`/`DELETE repos/OWNER/REPO/rulesets` as part of this scaffold and do not claim that a ruleset was configured.

Before changing classic protection or installing auto-merge, inspect the effective rules on the detected default branch with `repos/OWNER/REPO/rules/branches/BRANCH`. Report repository and organization rulesets as existing policy, preserve them, and stop when an effective rule conflicts with the proposed classic settings. In a ruleset `required_status_checks` rule, the source-binding field is `integration_id`, not classic protection's `app_id`. If the user wants a ruleset created or changed, treat that as a separate policy-design task requiring explicit approval and a complete reviewed payload.

## Labels

GitHub creates these default labels for new repositories, but they can be edited
or deleted. Recreate them if missing:

```powershell
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

function Add-LabelIfMissing {
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Color,
    [Parameter(Mandatory)][string]$Description
  )

  if ($existingLabels.Contains($Name)) { return }
  $createOutput = gh label create $Name --repo github.com/OWNER/REPO `
    --color $Color --description $Description 2>&1
  $createExitCode = $LASTEXITCODE

  # Re-read the label even after a failed create. Another actor may have created it
  # after the initial list, and a successful response still needs state verification.
  $encodedName = [Uri]::EscapeDataString($Name)
  $labelOutput = gh api --hostname github.com `
    "repos/OWNER/REPO/labels/$encodedName" 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Could not verify label '$Name' after the create attempt. Create result: $($createOutput | Out-String) Verification result: $($labelOutput | Out-String)"
  }
  $label = ($labelOutput | Out-String) | ConvertFrom-Json
  $labelMatches = [StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$label.name,
    $Name
  ) -and [StringComparer]::OrdinalIgnoreCase.Equals(
    ([string]$label.color).TrimStart("#"),
    $Color.TrimStart("#")
  ) -and [string]$label.description -ceq $Description
  if (-not $labelMatches) {
    throw "Label '$Name' exists after the create attempt but does not match the requested color and description. It was not overwritten."
  }
  if ($createExitCode -ne 0) {
    Write-Warning "The label create call failed, but a concurrent matching label now exists. $($createOutput | Out-String)"
  }
  [void]$existingLabels.Add($Name)
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

Create workflow/config labels only when shipping the matching optional asset:

```powershell
# auto-merge.yml
Add-LabelIfMissing "automerge" 0e8a16 "Auto-merge when CI is green"

# labeler.yml
Add-LabelIfMissing "ci"    5319e7 "Changes to CI configuration"
Add-LabelIfMissing "tests" bfdadc "Changes to tests"

# release-config.yml
Add-LabelIfMissing "feature"            a2eeef "Feature work for release notes"
Add-LabelIfMissing "fix"                d73a4a "Bug fix for release notes"
Add-LabelIfMissing "ignore-for-release" ffffff "Exclude from generated release notes"

# stale.yml
Add-LabelIfMissing "Stale"    ededed "Inactive issue or pull request"
Add-LabelIfMissing "pinned"   1d76db "Exempt from stale automation"
Add-LabelIfMissing "security" b60205 "Security-related work or report"
```

## Dependabot

Preserve the asset's fixed root `pip` block because every scaffold installs `requirements-docs.txt`, even when the project has no application manifest. Render additional blocks only from manifests that actually exist in the filtered first-party candidate list. Exclude any path with a `node_modules`, `.venv`, `venv`, `vendor`, `dist`, `build`, or `target` component even when tracked, unless the user explicitly confirms that path is a first-party workspace. For every supported application ecosystem, use GitHub's exact `package-ecosystem` identifier and the repository-relative directory containing its manifest. Do not emit a duplicate root `pip` block for a root Python manifest; the fixed block already covers every supported root requirements file. Use `directories` for multiple non-overlapping additional locations of the same ecosystem, or separate blocks when their settings differ. Give every generated block `commit-message.prefix: "chore(deps)"`; Dependabot applies that prefix to both commit messages and PR titles, allowing them to pass `commitlint.yml` when that optional workflow is installed. Retain the asset's fixed `github-actions` block with `directory: "/"` so pinned workflow actions remain updateable.

Example replacement for the entire `  # {{REPO_SCAFFOLD_DEPENDABOT_PACKAGE_UPDATES}}` marker line when npm manifests exist at the root and under `packages/web`:

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

When there is no additional supported application location, delete the marker line and keep the fixed root `pip` and `github-actions` blocks. Otherwise, the replacement must end with a newline so the following `github-actions` list item remains valid YAML. Validate both the asset and the rendered file, and require only that the exact `{{REPO_SCAFFOLD_DEPENDABOT_PACKAGE_UPDATES}}` token is gone. Do not use a generic double-brace regex: project-owned Helm, Jinja, or Angular expressions are not repo-scaffold placeholders, and `${{ ... }}` is valid GitHub Actions syntax. Once committed as `.github/dependabot.yml`, GitHub picks it up automatically; no API call is needed.

## Security features

Inspect repository capabilities before changing settings:

```bash
gh repo view github.com/OWNER/REPO --json visibility,isFork,isArchived,hasIssuesEnabled,hasDiscussionsEnabled,owner,defaultBranchRef
gh api --hostname github.com users/OWNER --jq '.type'
gh api --hostname github.com repos/OWNER/REPO --jq '.security_and_analysis'
```

The repository view returns the owner login but not its account type. Treat the separate Users API result as `OWNER_TYPE`, accept only `User` or `Organization`, and ask instead of guessing when the lookup fails or returns another value. Use this value for CODEOWNERS selection and private dependency-review eligibility.

Run each eligible command separately after confirmation and only report a feature as enabled after its final state is verified. Treat `403` as forbidden and `404` as missing or inaccessible unless endpoint-specific evidence proves a capability limitation. Preserve and inspect every `422` response: depending on the endpoint it can indicate invalid input, ineligibility, or abuse controls, so do not automatically relabel it as a plan or permission failure. Treat `409` as an in-progress/conflicting operation and `503` as transient service unavailability. Report the verified capability, validation, or retry result and continue instead of claiming success or failing the entire scaffold.

- **Dependency graph**: enabled by default for public repositories. It is not the same setting as Dependabot alerts.
- **Dependabot alerts**: not enabled by default. Enable explicitly where supported, then optionally enable security updates:

  ```bash
  gh api --hostname github.com -X PUT repos/OWNER/REPO/vulnerability-alerts
  gh api --hostname github.com -X PUT repos/OWNER/REPO/automated-security-fixes
  ```

- **Secret scanning + push protection**: availability depends on repository visibility and the owner's GitHub security entitlement. Push protection requires secret scanning. Attempt only after the capability check:

  ```bash
  gh repo edit github.com/OWNER/REPO --enable-secret-scanning --enable-secret-scanning-push-protection
  ```

- **CodeQL advanced setup**: when the user explicitly chooses a repository-managed configuration, install `assets/workflows/codeql.yml`, render the verified default branch through `{{REPO_SCAFFOLD_DEFAULT_BRANCH_GLOB_JSON_ESCAPED}}` plus a supported detected language, and keep CodeQL default setup not configured. Inspect workflows and existing analyses first, and do not install a second advanced uploader silently. If default setup is already configured, stop and obtain explicit approval before switching modes.

- **Code scanning default setup**: requires an eligible repository and supported detected language. Skip this mutation path when the repository-managed advanced workflow was selected. Otherwise, first inspect the current default-setup state, direct workflow evidence in the working tree and default branch, and existing CodeQL analyses. Separately ask whether external CI, indirect scripts, local actions, composite actions, or any other process uploads CodeQL results. Do not infer their absence from repository workflow inspection. Do not treat a generic request to enable code scanning as permission to replace advanced setup: switching disables its workflow and blocks CodeQL analysis API uploads.

  The bundled preflight requires PyYAML and a Python feature release at or above
  `tooling-python-minimum` in `.github/ci-toolchain.json`.

  Resolve `REPO_SCAFFOLD_SKILL_ROOT` to the installed/source directory that contains this skill's `SKILL.md`; do not guess it from the current working directory. Run the bundled structural preflight with an available Python interpreter. It uses PyYAML's non-coercing `BaseLoader`, rejects duplicate keys, inspects only direct files under `.github/workflows`, inspects semantic `jobs.*.uses`, `jobs.*.steps[*].uses`, and shell-aware executable `run` content, and honors step, job, and workflow shell selection. For recognized Bash and PowerShell shells it masks inert heredoc, here-string, arithmetic-shift, literal, comment, and uninvoked function content, including function definitions whose opening brace is on the following line. It retains transitively invoked function bodies, literal `eval` and trap handlers, exported functions invoked by literal nested-shell commands, statically resolvable Bash/PowerShell aliases, direct shell-heredoc, recognized command wrappers, GNU `env` split strings, `xargs` with supported GNU/BSD options, direct `find` executors, shell `-c`, pipeline-fed shells, backtick/`$()` command substitution, Bash process substitution, PowerShell scriptblocks, nested PowerShell `-Command`, `Invoke-Expression`, `Start-Process`, direct `cmd /c` or `/k` CodeQL commands, quoted call-operator commands, and PowerShell `$()` execution. An unresolved command position, call-operator expression, recognized dynamic executor or alias target, encoded PowerShell command with a non-literal payload, or a malformed or unterminated construct fails closed. An unsupported or unresolved effective shell also fails closed instead of falling back to raw-text inspection. If default setup is already configured, it returns the safe preserve decision without the unnecessary workflow/analysis queries, sets those uninspected evidence fields to `null`, and sets `workflow_inspection_performed` and `analysis_inspection_performed` to false. Any other state must be exactly `not-configured`; an unknown default-setup state fails closed. It follows reusable workflows per top-level caller, rejects cycles, enforces GitHub's limit of 50 unique called workflows and 10 total levels on every call path, retains a separate 500-edge traversal safety cap, bounds API requests, and applies a timeout to each `gh api` subprocess. If Python, PyYAML, the effective shell, shell syntax, a workflow, a linked path, an API response, or the separate external/indirect CodeQL confirmation is unavailable, it exits inconclusive and mutation remains forbidden.

  Initialize `$noExternalCodeqlConfirmed` to `$false`. Set it to `$true` only after the user explicitly confirms that no external CI, indirect script, local action, composite action, or other process uploads CodeQL results. This confirmation is distinct from general scaffold approval and from approval to switch when advanced-setup evidence exists.

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
  $defaultSetupMutationApproved = $false
  $noExternalCodeqlConfirmed = $false

  if ($null -eq $pythonCommand) {
    Write-Warning "No Python $minimumPython or newer interpreter with PyYAML is available for structural workflow inspection; do not PATCH default setup."
  } else {
    $preflightArguments = @(
      "--repo-root", $REPO_ROOT,
      "--repository", "OWNER/REPO",
      "--default-branch", $DEFAULT_BRANCH,
      "--hostname", "github.com"
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
        $preflight = ($preflightOutput | Out-String) | ConvertFrom-Json
      } catch {
        throw "CodeQL preflight returned invalid JSON; do not PATCH."
      }
      if (-not $preflight.inspection_complete) {
        Write-Warning "CodeQL setup inspection is inconclusive; do not PATCH."
      } elseif ($preflight.decision -eq "preserve-default-setup") {
        Write-Output "Code scanning default setup is already configured; preserve it without PATCHing."
      } elseif ($preflight.decision -eq "require-explicit-switch-confirmation") {
        $workflowSummary = @($preflight.advanced_workflows) -join ", "
        Write-Warning "Possible CodeQL advanced setup detected ($workflowSummary). Stop before PATCH, explain that default setup will disable the existing workflow and block CodeQL analysis API uploads, and ask for explicit approval to switch."
      } elseif ($preflight.decision -eq "may-offer-default-setup") {
        $defaultSetupMutationApproved = $true
        Write-Output "No direct CodeQL evidence was detected, and the absence of external or indirect CodeQL uploads was separately confirmed; default setup may be offered."
      } else {
        Write-Warning "CodeQL preflight returned an unknown decision; do not PATCH."
      }
    }
  }
  ```

  Run the mutating block below in the same PowerShell session only when the inspection completed, default setup is not already configured, and either no direct or confirmed-external advanced-setup evidence exists or the user separately confirmed the documented switch. After that switch confirmation, set `$defaultSetupMutationApproved = $true` explicitly in that session. Record both kinds of confirmation; do not infer either from general scaffold approval. The PATCH can return `202 Accepted` with a validation workflow; wait for that run to complete and require a final GET with `state: configured` before calling the feature enabled. A non-successful validation conclusion does not negate `state: configured`; report that conclusion separately and do not claim that scans succeeded:

  ```powershell
  if (-not $defaultSetupMutationApproved) {
    throw "CodeQL default-setup mutation was not approved by the completed preflight."
  }
  $defaultSetupPath = "repos/OWNER/REPO/code-scanning/default-setup"
  $defaultSetupEnabled = $false
  $setupOutput = & gh api --hostname github.com -X PATCH $defaultSetupPath -f state=configured 2>&1
  $setupExitCode = $LASTEXITCODE

  if ($setupExitCode -ne 0) {
    $setupError = $setupOutput | Out-String
    if ($setupError -match '(?is)HTTP 403') {
      Write-Warning "GitHub forbade the default-setup request; verify eligibility and permission, then continue without claiming enablement. $setupError"
    } elseif ($setupError -match '(?is)HTTP 404') {
      Write-Warning "The default-setup endpoint or repository is missing, unavailable, or inaccessible; continue without claiming enablement. $setupError"
    } elseif ($setupError -match '(?is)HTTP 422') {
      Write-Warning "GitHub rejected the default-setup request as invalid, ineligible, or abuse-limited; inspect this response and continue without claiming enablement. $setupError"
    } elseif ($setupError -match '(?is)HTTP 409') {
      Write-Warning "A different default-setup validation run is already in progress; retry after it completes."
    } elseif ($setupError -match '(?is)HTTP 503') {
      Write-Warning "Code scanning default setup is temporarily unavailable; retry later."
    } else {
      Write-Warning "Code scanning default setup failed; continue without changing its reported state. $setupError"
    }
  } else {
    $setupResponse = ($setupOutput | Out-String) | ConvertFrom-Json
    $validationFinished = $null -eq $setupResponse.run_id
    $validationPollingFailed = $false
    $validationConclusion = $null

    if ($null -ne $setupResponse.run_id) {
      $validationFinished = $false
      # Keep one polling batch bounded so Codex can report progress and resume instead
      # of holding a single tool call open for up to ten minutes.
      for ($attempt = 0; $attempt -lt 4; $attempt++) {
        $runOutput = gh api --hostname github.com "repos/OWNER/REPO/actions/runs/$($setupResponse.run_id)" 2>&1
        if ($LASTEXITCODE -ne 0) {
          $validationPollingFailed = $true
          Write-Warning "Could not verify the default-setup validation run; do not claim enablement. $($runOutput | Out-String)"
          break
        }
        $run = ($runOutput | Out-String) | ConvertFrom-Json
        if ($run.status -eq "completed") {
          $validationFinished = $true
          $validationConclusion = $run.conclusion
          if ($validationConclusion -ne "success") {
            Write-Warning "Default-setup validation completed with '$validationConclusion'. Query the final setup state, and if it is configured report the unsuccessful validation separately from enablement."
          }
          break
        }
        if ($attempt -lt 3) { Start-Sleep -Seconds 10 }
      }
      if (-not $validationFinished -and -not $validationPollingFailed) {
        Write-Warning "Default-setup validation is still running (run_id $($setupResponse.run_id)). Stop this polling batch without claiming enablement, report progress, and resume verification in a later tool call."
      }
    }

    if ($validationFinished -and -not $validationPollingFailed) {
      $verifyOutput = gh api --hostname github.com $defaultSetupPath 2>&1
      if ($LASTEXITCODE -eq 0) {
        $verifiedSetup = ($verifyOutput | Out-String) | ConvertFrom-Json
        $defaultSetupEnabled = $verifiedSetup.state -eq "configured"
      } else {
        Write-Warning "The validation run finished, but the final default-setup state could not be queried. Do not claim enablement. $($verifyOutput | Out-String)"
      }
      if (-not $defaultSetupEnabled) {
        Write-Warning "The final default-setup state is not verified as configured."
      } elseif ($null -ne $validationConclusion -and $validationConclusion -ne "success") {
        Write-Warning "Code scanning default setup is configured, but its validation concluded '$validationConclusion'; report both facts and do not claim successful scans."
      }
    }
  }

  if ($defaultSetupEnabled) {
    Write-Output "Code scanning default setup is enabled and verified."
  }
  ```

  When the bounded batch reports that validation is still running, return a progress update instead of extending the same blocking command. In a later tool call, query the recorded `run_id` again with `repos/OWNER/REPO/actions/runs/RUN_ID`; after it completes, re-query `$defaultSetupPath` regardless of conclusion. Treat `state: configured` as enablement, and report any non-successful validation conclusion separately without claiming that scans succeeded.

- **Private vulnerability reporting**: despite its name, this repository setting is for receiving reports privately on a public repository. Offer it only for a public, non-fork repository:

  ```bash
  gh api --hostname github.com -X PUT repos/OWNER/REPO/private-vulnerability-reporting
  ```

- **Dependency review workflow**: install `assets/workflows/dependency-review.yml` for public repositories, or for organization-owned private or internal repositories only after confirming GitHub Code Security/Advanced Security eligibility. The v5 asset handles both `pull_request` and `merge_group` payloads. Require its `dependency-review` check only when the workflow can run on every event required by the repository's effective rules.

## Merge settings

Match the squash-default PR flow and keep branches tidy:

On GitHub.com, auto-merge is available for private repositories only with GitHub Pro, Team, or Enterprise Cloud. Check the repository plan/capability first. Treat `403` as forbidden; preserve a `422` response and report it as a rejected or ineligible configuration without guessing that token permission caused it. Do not install an auto-merge workflow unless enablement succeeds.

Before installing either shipped auto-merge workflow, inspect the effective rules on the detected default branch. The built-in `GITHUB_TOKEN` cannot add a pull request to a merge queue, and the shipped workflows are intentionally not queue workflows:

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

Continue with the repository merge settings below even when `$hasMergeQueue` is true, but preserve every merge method used by an effective merge queue or allowed by an effective pull-request rule. Install `auto-merge.yml` or `dependabot-auto-merge.yml` only when `$hasMergeQueue` is false.

```powershell
# Default to squash-only only when effective rules do not require or allow another
# repository-level merge method. A queue configured for MERGE or REBASE must keep
# that method enabled or GitHub will block the queue.
$enableMergeCommit = $requiredRepositoryMergeMethods.Contains("merge")
$enableRebaseMerge = $requiredRepositoryMergeMethods.Contains("rebase")
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

  $squashTitleOutput = & gh api --hostname github.com -X PATCH `
    repos/OWNER/REPO -f squash_merge_commit_title=PR_TITLE 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure the squash commit title. $($squashTitleOutput | Out-String)"
  }
  $completedMergeUpdates += "squash_merge_commit_title"

  # Enable this optional capability separately. Only install auto-merge workflows
  # after this call and the final state verification succeed.
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
if (-not [bool]$finalMergeSettings.allow_auto_merge) {
  $mergeSettingProblems += "auto-merge is disabled"
}
if ([string]$finalMergeSettings.squash_merge_commit_title -cne "PR_TITLE") {
  $mergeSettingProblems += "squash commit title is not PR_TITLE"
}
if ($mergeSettingProblems.Count -gt 0) {
  throw "Repository merge settings did not reach the requested state: $($mergeSettingProblems -join '; '). Final state: $finalMergeSummary"
}
```

The repository API setting `squash_merge_commit_title=PR_TITLE` makes the final squash commit use the PR title. When `commitlint.yml` is installed, its dependency-free `commitlint` job validates that title as well as the PR commits, preserving Conventional Commit input for release-please. `--enable-auto-merge` is required for any auto-merge workflow (`gh pr merge --auto`) to work — both Dependabot auto-merge and the label-gated `auto-merge.yml`.

## release-please token (RELEASE_PLEASE_TOKEN)

Treat plugin-creator's local `+codex.<cachebuster>` suffix as installation identity only. Do not copy it into the public release manifest, plugin version, changelog, or tag; confirm and use the clean public SemVer instead. Preserve other SemVer build metadata only when the user explicitly confirms it is part of the public release identity.

The shipped `release.yml` also supports a verified manual recovery path without a `push.tags` trigger. Run it only after the exact tag exists and resolves to the supplied full commit SHA:

```bash
gh workflow run release.yml --repo OWNER/REPO --ref DEFAULT_BRANCH \
  -f tag=vX.Y.Z -f commit_sha=FULL_COMMIT_SHA
```

Only when the repo ships `release-please.yml` (or `auto-merge.yml`, which prefers this token). Use a fine-grained PAT so release-please-created PRs can trigger their required checks and merges performed by the label-gated workflow can trigger the default-branch release-please run; events created by the default `GITHUB_TOKEN` do not start another workflow run.

`auto-merge.yml` may read this PAT only from its trusted-base `pull_request_target` workflow after the human-user and same-repository guards pass. Never change that workflow to `pull_request` while it references the PAT, and never add a checkout, fetch, artifact download, or command that executes PR-controlled content.

Use these exact Release Please locale values so repeated scaffolds remain
deterministic:

| Locale | Pull-request title | Header | Footer lead-in |
| --- | --- | --- | --- |
| `en` | `chore${scope}: release${component} ${version}` | `:robot: I have created a release *beep* *boop*` | `This PR was generated with` |
| `vi` | `chore${scope}: phát hành${component} ${version}` | `:robot: Release Please đã tạo PR phát hành tự động này.` | `PR này được tạo tự động bằng` |

For `en`, use `Features`, `Bug Fixes`, `Performance Improvements`, `Reverts`,
`Documentation`, `Styles`, `Miscellaneous Chores`, `Code Refactoring`, `Tests`,
`Build System`, and `Continuous Integration`. For `vi`, use `Tính năng`,
`Sửa lỗi`, `Cải thiện hiệu năng`, `Hoàn tác`, `Tài liệu`, `Định dạng mã`,
`Bảo trì khác`, `Tái cấu trúc mã`, `Kiểm thử`, `Hệ thống xây dựng`, and
`Tích hợp liên tục`. Keep the repeated `feat` and `feature` entries and every
`hidden` flag from the asset unchanged. Complete the footer with the same
Release Please and documentation links from the English asset, translating
only the surrounding prose.

When installing release-please, also copy `assets/release-please-config.json` and `assets/release-please-manifest.json` to the repository root as `release-please-config.json` and `.release-please-manifest.json`. Render the config's pull-request title, header, footer, and changelog section names in the resolved `SCAFFOLD_LANGUAGE`. Preserve `${scope}`, `${component}`, and `${version}` exactly in the title pattern, and preserve the asset's changelog type order and `hidden` flags so localization does not change release semantics. Before changing the title pattern in an existing setup, list open release PRs and coordinate the transition: release-please uses the configured pattern to build and parse titles, so rename an existing release PR to the exact new pattern immediately before the config lands, or wait until that PR is resolved. After the first run with the new config, verify that the original release PR was updated and no duplicate was opened. The config intentionally combines `draft: true` with `force-tag-creation: true`. release-please creates the tag before it creates the draft Release, so never pair this mode with `release-tag.yml` or another `push.tags: v*` caller: that caller could observe the tag before the draft exists. The shipped `release-please.yml` instead waits for the release-please action to complete, then invokes reusable `release.yml` with the emitted tag and the action's `sha` output. The engine serializes callers by tag. A read-only build job verifies the tag through the authenticated Git database references/tags REST APIs, checks out that immutable commit without persisted credentials, builds and validates regular-file artifacts, then transfers them through SHA-pinned artifact actions. For an eligible repository, a fresh attestation job downloads those files without a checkout, validates them without executing project code, and generates SLSA build provenance with `actions/attest`; it alone receives `id-token: write` and `attestations: write`, while the reusable-workflow caller passes those permissions through. GitHub currently supports attestations for public repositories on current plans and for private/internal repositories on GitHub Enterprise Cloud. For an ineligible repository, render the documented no-attestation variant rather than leaving a gate that cannot succeed. A separate write-enabled publish job on a fresh runner downloads but never executes those artifacts, waits for attestation when enabled, verifies the tag immediately before publishing, and checks it once more after publication. When it creates a missing draft Release, `--verify-tag` prevents GitHub CLI from silently recreating a tag that disappeared after verification. A pre-publication mismatch leaves the Release as a draft and fails. A post-publication mismatch is an integrity incident that the workflow reports but cannot roll back; use an effective tag ruleset or immutable releases when tag movement must be prevented rather than merely detected. Reruns may repair a draft, but they must refuse every published Release, including a legacy mutable one: `gh release upload --clobber` deletes an existing public asset before uploading its replacement, so an upload failure can lose the original. Create a new version tag instead. Fill the manifest with a confirmed current version without a leading `v`; do not invent an initial version. Do not remove either option or collapse the build, eligible attestation, and publish permission boundaries while `release.yml` is responsible for artifacts.

After downloading a published artifact, verify both its provenance and the reusable signer workflow:

```bash
gh attestation verify PATH/TO/ARTIFACT \
  --repo OWNER/REPO \
  --signer-workflow OWNER/REPO/.github/workflows/release.yml
```

1. Create the PAT (GitHub UI → Settings → Developer settings → Fine-grained tokens, or `gh` if available). Scope it least-privilege: **only this repository**, permissions **Contents: Read and write** + **Pull requests: Read and write** (add **Issues: Read and write** if release-please manages issues). Nothing else.
2. Add it as a repository secret named **exactly** `RELEASE_PLEASE_TOKEN` (Settings → Secrets and variables → Actions → New repository secret). The name must match the `secrets.RELEASE_PLEASE_TOKEN` reference in the workflows character-for-character — secret names allow only letters, digits, and underscores (no hyphens/spaces), so a mismatch makes the action fail with "Input required: token".

Never paste the token value into a chat, commit, or log. If one is ever exposed, revoke it immediately and create a new one.

## Verify

Read visibility, fork state, and the default branch first:

```bash
gh repo view github.com/OWNER/REPO --json visibility,isFork,defaultBranchRef
```

- For any non-fork repository, verify community profile health. Public resources can be queried without authentication; private repositories require an authenticated token with `Contents: read`:

  ```bash
  gh api --hostname github.com repos/OWNER/REPO/community/profile --jq '.health_percentage'
  ```

- Skip the community-profile call only for forks. For a private non-fork repository, run it with authenticated `gh`; if it fails, report the permission/API error rather than claiming private repositories are unsupported.
- Confirm license detection:

  ```bash
  gh api --hostname github.com repos/OWNER/REPO/license --jq '.license.spdx_id'
  ```

- Verify the selected protection mechanism on the detected default branch. URL-encode the branch because valid branch names can contain `/`:

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

Run the classic command when this plugin configured classic protection. Always run the effective-rules command to identify preserved repository or organization rulesets; this is inspection, not proof that the plugin configured them. For classic protection, compare every returned check `context` and `app_id` with the exact `$requiredCheckNames` and `$requiredAppIdsByContext` used during setup. For an effective ruleset `required_status_checks` rule, inspect its `context` and `integration_id` separately and report conflicts with the intended checks; never compare a ruleset field to classic `app_id`. Re-query both Check Runs and Commit Statuses on each representative PR's head and current test-merge SHAs, recent default-branch SHAs, and a merge-group SHA when applicable. Apply test-merge precedence when it has statuses, verify that each context corresponds to exactly one effective job name and the same GitHub App, and fail verification if a same-name Commit Status exists on any controlling SHA. Remove, rename, or correct a classic context that no workflow emits, that multiple workflows emit, whose app binding differs, or that collides across the two status systems. Preserve existing rulesets unless the user starts a separate approved ruleset-policy change.
