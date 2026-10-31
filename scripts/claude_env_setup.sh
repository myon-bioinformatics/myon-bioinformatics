#!/usr/bin/env bash
# Claude Code 作業環境を新しい版にそろえる（何度流しても同じ結果になる）。
#
#   bash claude_env_setup.sh            # 足りないものだけ入れる（2 回目以降は数秒）
#   UPDATE=1 bash claude_env_setup.sh   # conda-forge の道具も最新に更新する
#
# 入れるもの: Node の LTS 系列の最新（npm 付き）、uv と pip、uv 管理の Python 3.14/3.15（既定は 3.14）、
#             git gh go ruby jq ripgrep pandoc ffmpeg curl（conda-forge。専用環境 /opt/modern）。
# 触らないもの: Playwright とブラウザ（新しいブラウザの配布元に届かず、版を上げると壊れる）、
#             /opt/npm-tools（イメージ側で版を固定）、OS 管理の Python パッケージ。
# 前提: root、PyPI・npm・conda.anaconda.org に届くこと。公式の nodejs.org・go.dev・GitHub Releases には届かない。
# 各段は独立で、1 つ失敗しても残りを続ける。失敗した段があれば終了コードは 1。
# ログは /tmp/claude-env-setup.log。source しただけでは実行しない。
#
# 新しいセッションでも効かせる: Claude Code の環境設定の「セットアップスクリプト」欄に次の 1 行を貼る
#   curl -fsSL https://raw.githubusercontent.com/myon-bioinformatics/myon-bioinformatics/main/scripts/claude_env_setup.sh | bash
# （更新はこのファイルを直すだけでよい。Node の LTS が変わったら NODE_MAJOR だけ変える）
set -u
NODE_MAJOR=${NODE_MAJOR:-26}           # LTS 系列。次の LTS が出たらここだけ変える
NPM_MAJOR=${NPM_MAJOR:-11}             # その Node に付いてくる npm の系列
PY_DEFAULT=${PY_DEFAULT:-3.14}         # uv の既定 Python。切り替えは uv python pin --global 3.15 など
MODERN=${MODERN:-/opt/modern}
LINK_DIR=${LINK_DIR:-${HOME}/.npm-global/bin}   # PATH の先頭にあるディレクトリ（現在のシェルで効かせる）
TOOLS="go ruby git gh jq ripgrep pandoc ffmpeg curl"
LINKS="git gh go gofmt ruby gem irb bundle bundler rake jq rg pandoc ffmpeg ffprobe curl"
LOG=/tmp/claude-env-setup.log
say() { printf '%s\n' "$*" | tee -a "$LOG"; }
step() { say "== $1"; }
ver() { "$@" 2>&1 | head -1 | cut -c1-70; }
link_into() { # link_into 実体 名前 ... : 名前ごとに LINK_DIR へ張る
  mkdir -p "$LINK_DIR" || return 1
  local src=$1 n; shift
  for n in "$@"; do
    if [ -e "$src/$n" ]; then ln -sfn "$src/$n" "$LINK_DIR/$n" || return 1; fi
  done
}

node_step() {
  local d=/opt/node$NODE_MAJOR want have versions
  versions=$(npm view "node@^$NODE_MAJOR" version 2>>"$LOG") || return 1
  want=$(printf '%s\n' "$versions" | tail -1 | sed "s/.*'\(.*\)'.*/\1/")
  [ -n "$want" ] || { say "Node: 最新版を調べられない"; return 1; }
  have=$("$d/bin/node" --version 2>/dev/null | tr -d v)
  if [ "$have" != "$want" ]; then
    local t; t=$(mktemp -d) || return 1
    if ! npm install --prefix "$t" --no-audit --no-fund --silent "node@$want" >>"$LOG" 2>&1 \
      || ! mkdir -p "$d/bin" "$d/lib" \
      || ! install -m 755 "$t/node_modules/node/bin/node" "$d/bin/node.new" \
      || ! mv -f "$d/bin/node.new" "$d/bin/node"; then
      rm -rf "$t"
      return 1
    fi
    rm -rf "$t" || return 1
  fi
  # Node の更新後に npm が失敗しても、次回は npm 単独で修復する。
  local npm_have npx_have
  npm_have=$("$d/bin/npm" --version 2>/dev/null) || npm_have=""
  npx_have=$("$d/bin/npx" --version 2>/dev/null) || npx_have=""
  if [[ "$npm_have" != "$NPM_MAJOR".* || "$npx_have" != "$NPM_MAJOR".* ]]; then
    mkdir -p "$d/lib" || return 1
    npm install --prefix "$d/lib" --no-audit --no-fund --no-save --silent "npm@$NPM_MAJOR" >>"$LOG" 2>&1 || return 1
    rm -f "$d/lib/package.json" "$d/lib/package-lock.json" || return 1
    ln -sfn ../lib/node_modules/npm/bin/npm-cli.js "$d/bin/npm" || return 1
    ln -sfn ../lib/node_modules/npm/bin/npx-cli.js "$d/bin/npx" || return 1
  fi
  "$d/bin/node" --version >>"$LOG" 2>&1 && "$d/bin/npm" --version >>"$LOG" 2>&1 \
    && "$d/bin/npx" --version >>"$LOG" 2>&1 || return 1
  for b in node npm npx; do ln -sfn "$d/bin/$b" "/usr/local/bin/$b" || return 1; done
  link_into "$d/bin" node npm npx || return 1
  printf 'export PATH=%s/bin:$PATH\n' "$d" > /etc/profile.d/nodejs.sh || return 1
  say "Node $("$d/bin/node" --version) / npm $("$d/bin/npm" --version)"
}

python_step() {
  python3 -m pip install --quiet --upgrade --root-user-action=ignore pip uv >>"$LOG" 2>&1 || return 1
  mkdir -p /root/.local/bin || return 1
  ln -sfn /usr/local/bin/uv /root/.local/bin/uv || return 1
  ln -sfn /usr/local/bin/uvx /root/.local/bin/uvx || return 1
  uv python install 3.14 3.15 >>"$LOG" 2>&1 || return 1
  uv python upgrade 3.14 >>"$LOG" 2>&1 || return 1
  # Python は uv を正とする: 管理版を優先し、どこでも既定は $PY_DEFAULT（リポジトリ側の指定が優先）。
  # システムの python3（スキルが使うライブラリ入り）は差し替えない
  mkdir -p /root/.config/uv || return 1
  grep -qs '^python-preference' /root/.config/uv/uv.toml || printf 'python-preference = "managed"\n' >> /root/.config/uv/uv.toml || return 1
  uv python pin --global "$PY_DEFAULT" >>"$LOG" 2>&1 || return 1
  # 社内ミラーに無い版を取りこぼさないよう、uv ツールは PyPI を指定して更新する
  uv tool upgrade --all --default-index https://pypi.org/simple >>"$LOG" 2>&1 || return 1
  say "$(uv --version | cut -c1-12) / $(python3 -m pip --version | cut -d' ' -f1-2) / $(uv python list --only-installed 2>/dev/null | awk "{print \$1}" | sort -u | wc -l) Python"
}

modern_step() {
  export MAMBA_ROOT_PREFIX=$MODERN/root MAMBA_SSL_VERIFY=${SSL_CERT_FILE:-/root/.ccr/ca-bundle.crt}
  local mm=$MODERN/bin/micromamba
  if [ ! -x "$mm" ]; then
    mkdir -p "$MODERN/bin" || return 1
    local t; t=$(mktemp -d) || return 1
    curl -fsS --max-time 180 -o "$t/mm.tar.bz2" https://conda.anaconda.org/conda-forge/linux-64/micromamba-2.10.0-0.tar.bz2 \
      && tar -xjf "$t/mm.tar.bz2" -C "$t" bin/micromamba && install -m 755 "$t/bin/micromamba" "$mm"
    rm -rf "$t"
  fi
  [ -x "$mm" ] || { say "micromamba を取得できない"; return 1; }
  local n missing=0 required="git gh go ruby jq rg pandoc ffmpeg curl"
  for n in $required; do [ -x "$MODERN/env/bin/$n" ] || missing=1; done
  if [ ! -d "$MODERN/env/conda-meta" ]; then
    "$mm" create -y -q -p "$MODERN/env" -c conda-forge --override-channels --no-rc $TOOLS >>"$LOG" 2>&1 || return 1
  elif [ "$missing" = 1 ]; then
    "$mm" install -y -q -p "$MODERN/env" -c conda-forge --override-channels --no-rc $TOOLS >>"$LOG" 2>&1 || return 1
  elif [ "${UPDATE:-0}" = 1 ]; then
    "$mm" update -y -q -p "$MODERN/env" -c conda-forge --override-channels --no-rc $TOOLS >>"$LOG" 2>&1 || return 1
  fi
  for n in $required; do [ -x "$MODERN/env/bin/$n" ] || return 1; done
  mkdir -p "$MODERN/links" || return 1
  link_into "$MODERN/env/bin" $LINKS || return 1
  for n in $LINKS; do
    if [ -e "$MODERN/env/bin/$n" ]; then ln -sfn "$MODERN/env/bin/$n" "$MODERN/links/$n" || return 1; fi
  done
  printf 'export PATH=%s/links:$PATH\n' "$MODERN" > /etc/profile.d/modern-tools.sh || return 1
  local E=$MODERN/env/bin
  say "git $("$E/git" --version | cut -d' ' -f3) / gh $("$E/gh" --version | head -1 | cut -d' ' -f3) / $("$E/go" version | cut -d' ' -f3) / ruby $("$E/ruby" -e 'print RUBY_VERSION') / jq $("$E/jq" --version) / rg $("$E/rg" --version | head -1 | cut -d' ' -f2) / pandoc $("$E/pandoc" --version | head -1 | cut -d' ' -f2) / ffmpeg $("$E/ffmpeg" -version | head -1 | cut -d' ' -f3) / curl $("$E/curl" --version | head -1 | cut -d' ' -f2)"
}

main() {
  : > "$LOG" || return 1
  local failed=0
  step "Node"; node_step || { say "  ! Node の更新に失敗（$LOG を参照）"; failed=1; }
  step "uv / pip / Python"; python_step || { say "  ! Python 側の更新に失敗（$LOG を参照）"; failed=1; }
  step "git・gh など（conda-forge）"; modern_step || { say "  ! conda-forge 側の更新に失敗（$LOG を参照）"; failed=1; }
  hash -r 2>/dev/null
  step "いまの PATH で見える版（各段の成功を保証するものではない）"
  say "node $(ver node --version) | git $(ver git --version) | gh $(ver gh --version) | uv $(ver uv --version)"
  return "$failed"
}

if [[ "${BASH_SOURCE[0]:-$0}" == "$0" ]]; then
  main "$@"
fi
