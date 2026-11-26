#!/usr/bin/env bash
# Resolve one stable upstream tag to its exact commit and upstream Bun pin.
set -euo pipefail
upstream=can1357/oh-my-pi
tag="${1:?Expected upstream stable tag}"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
refs=$(git ls-remote "https://github.com/$upstream.git" "refs/tags/$tag" "refs/tags/$tag^{}")
sha=$(printf '%s\n' "$refs" | awk '/\^\{\}$/ { print $1; found=1 } END { if (!found) exit 1 }' || true)
if [[ -z "$sha" ]]; then sha=$(printf '%s\n' "$refs" | awk 'NR == 1 { print $1 }'); fi
[[ "$sha" =~ ^[a-f0-9]{40}$ ]]
ci_content=$(gh api "repos/$upstream/contents/.github/workflows/ci.yml?ref=$sha" --jq .content | base64 -d)
bun=$(printf '%s\n' "$ci_content" | sed -nE "s/^[[:space:]]*bun-version:[[:space:]]*['\"]?([0-9]+\.[0-9]+\.[0-9]+)['\"]?[[:space:]]*(#.*)?$/\1/p" | sort -u)
bun_count=$(printf '%s\n' "$bun" | sed '/^$/d' | wc -l)
if [[ "$bun_count" -ne 1 ]]; then
  package_json=$(gh api "repos/$upstream/contents/package.json?ref=$sha" --jq .content | base64 -d)
  bun=$(printf '%s' "$package_json" | jq -r '.packageManager // empty' | sed -nE 's/^bun@[^0-9]*([0-9]+\.[0-9]+\.[0-9]+).*$/\1/p')
fi
[[ "$bun" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "::error::Could not resolve one supported Bun version from upstream CI or packageManager."
  exit 1
}

printf 'version=%s\nsha=%s\nbun=%s\n' "${tag#v}" "$sha" "$bun" >> "$GITHUB_OUTPUT"
