#!/bin/bash
# GaaS (Greg as a Service) installer / updater
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/gregology/GaaS/main/install.sh | bash
#
# Environment variables:
#   GAAS_HOME       Install directory (default: ~/.gaas)
#   GAAS_REPO       Git repository URL (default: https://github.com/gregology/GaaS.git)
#   GAAS_BIN_DIR    Wrapper script directory (default: ~/.local/bin)
#   GAAS_BRANCH     Branch to track (default: main)
#
# All code is wrapped in main() for partial-download protection.

set -euo pipefail

# ─── Constants ────────────────────────────────────────────────────────────────

GAAS_HOME="${GAAS_HOME:-$HOME/.gaas}"
GAAS_REPO="${GAAS_REPO:-https://github.com/gregology/GaaS.git}"
GAAS_BIN_DIR="${GAAS_BIN_DIR:-$HOME/.local/bin}"
GAAS_BRANCH="${GAAS_BRANCH:-main}"
WRAPPER="${GAAS_BIN_DIR}/gaas"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

# ─── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

# ─── Output helpers ───────────────────────────────────────────────────────────

info()    { printf "%b::%b %s\n" "${BLUE}" "${NC}" "$*"; }
success() { printf "%b✓%b %s\n" "${GREEN}" "${NC}" "$*"; }
warn()    { printf "%b!%b %s\n" "${YELLOW}" "${NC}" "$*"; }
error()   { printf "%b✗%b %s\n" "${RED}" "${NC}" "$*" >&2; }
fatal()   { error "$*"; exit 1; }

banner() {
    printf "%b%b" "${BOLD}" "${BLUE}"
    cat << 'BANNER'

   ____              ____
  / ___|  __ _  __ _/ ___|
 | |  _  / _` |/ _` \___ \
 | |_| || (_| | (_| |___) |
  \____| \__,_|\__,_|____/

  Greg as a Service

BANNER
    printf "%b" "${NC}"
}

# ─── Prerequisite checks ─────────────────────────────────────────────────────

check_os() {
    local os
    os="$(uname -s)"
    case "$os" in
        Linux|Darwin) success "Operating system: $os" ;;
        *) fatal "Unsupported operating system: $os (Linux and macOS only)" ;;
    esac
}

check_command() {
    local cmd="$1" name="${2:-$1}"
    if command -v "$cmd" &>/dev/null; then
        success "$name found: $(command -v "$cmd")"
        return 0
    fi
    return 1
}

check_git() {
    check_command git "Git" || fatal "Git is required but not found. Install it from https://git-scm.com/"
}

check_python() {
    local py_cmd=""
    for candidate in python3 python; do
        if command -v "$candidate" &>/dev/null; then
            local version
            version="$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)"
            if [ -n "$version" ]; then
                local major minor
                major="${version%%.*}"
                minor="${version#*.}"
                if [ "$major" -ge "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; then
                    py_cmd="$candidate"
                    success "Python $version found: $(command -v "$candidate")"
                    break
                fi
            fi
        fi
    done
    if [ -z "$py_cmd" ]; then
        fatal "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required but not found. Install from https://www.python.org/downloads/"
    fi
}

check_uv() {
    if check_command uv "uv"; then
        return 0
    fi
    warn "uv not found. It's required for dependency management."
    printf "\n"
    info "Install uv now? [Y/n] "
    local answer
    if [ -t 0 ]; then
        read -r answer
    else
        read -r answer < /dev/tty 2>/dev/null || answer="y"
    fi
    if [ "$answer" = "n" ] || [ "$answer" = "N" ]; then
        fatal "uv is required. Install manually: https://docs.astral.sh/uv/getting-started/installation/"
    fi
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the uv env so it's available in this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    check_command uv "uv" || fatal "uv installation failed. Install manually: https://docs.astral.sh/uv/getting-started/installation/"
}

check_gh() {
    if check_command gh "GitHub CLI"; then
        if gh auth status &>/dev/null 2>&1; then
            success "GitHub CLI authenticated"
        else
            warn "GitHub CLI found but not authenticated (run: gh auth login)"
        fi
    else
        warn "GitHub CLI not found (optional, needed for GitHub integration)"
        printf "  %bInstall: https://cli.github.com/%b\n" "${DIM}" "${NC}"
    fi
}

# ─── Installation ─────────────────────────────────────────────────────────────

clone_repo() {
    info "Cloning GaaS to $GAAS_HOME..."
    git clone --depth 1 --branch "$GAAS_BRANCH" "$GAAS_REPO" "$GAAS_HOME"
    success "Repository cloned"
}

install_deps() {
    info "Installing Python dependencies (this may take a moment)..."
    (cd "$GAAS_HOME" && uv sync --quiet)
    success "Dependencies installed"
}

write_wrapper() {
    mkdir -p "$GAAS_BIN_DIR"
    cat > "$WRAPPER" << SCRIPT
#!/bin/bash
# GaaS CLI wrapper — generated by install.sh
# Re-run the installer to regenerate, or edit GAAS_HOME below.
set -euo pipefail
GAAS_HOME="\${GAAS_HOME:-${GAAS_HOME}}"
if [ ! -d "\$GAAS_HOME" ]; then
    echo "Error: GaaS not found at \$GAAS_HOME" >&2
    echo "Re-run the installer or set GAAS_HOME to the correct path." >&2
    exit 1
fi
export PYTHONPATH="\$GAAS_HOME\${PYTHONPATH:+:\$PYTHONPATH}"
exec uv run --project "\$GAAS_HOME" python -m app.cli "\$@"
SCRIPT
    chmod +x "$WRAPPER"
    success "CLI wrapper written to $WRAPPER"
}

add_to_path() {
    # Check if BIN_DIR is already in PATH
    if echo "$PATH" | tr ':' '\n' | grep -qx "$GAAS_BIN_DIR"; then
        return 0
    fi

    local line="export PATH=\"${GAAS_BIN_DIR}:\$PATH\""
    local marker="# GaaS"
    local modified=false
    local shell_name
    shell_name="$(basename "${SHELL:-/bin/bash}")"

    # Determine which RC files to update based on the user's shell
    local rc_files=()
    case "$shell_name" in
        zsh)  rc_files=("$HOME/.zshrc") ;;
        bash)
            # .bash_profile for login shells (macOS default), .bashrc for interactive
            if [ "$(uname -s)" = "Darwin" ]; then
                rc_files=("$HOME/.bash_profile" "$HOME/.bashrc")
            else
                rc_files=("$HOME/.bashrc")
            fi
            ;;
        *)    rc_files=("$HOME/.profile") ;;
    esac

    for rc in "${rc_files[@]}"; do
        if [ -f "$rc" ] && grep -qF "$marker" "$rc"; then
            continue  # Already added
        fi
        printf '\n%s\n%s\n' "$marker" "$line" >> "$rc"
        modified=true
        success "Added $GAAS_BIN_DIR to PATH in $rc"
    done

    if [ "$modified" = true ]; then
        warn "Restart your shell or run: source ${rc_files[0]}"
    fi

    # Make it available in this session too
    export PATH="${GAAS_BIN_DIR}:$PATH"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

main() {
    banner

    # If GaaS is already installed and the wrapper exists, delegate to gaas update
    if [ -x "$WRAPPER" ] && [ -d "$GAAS_HOME/.git" ]; then
        info "Existing GaaS installation detected at $GAAS_HOME"
        info "Running update..."
        printf "\n"
        export PATH="${GAAS_BIN_DIR}:$PATH"
        exec gaas update
    fi

    printf "%b  Installing to: %s%b\n" "${DIM}" "$GAAS_HOME" "${NC}"
    printf "%b  CLI wrapper:   %s%b\n\n" "${DIM}" "$WRAPPER" "${NC}"

    # ── Prerequisites ──
    info "Checking prerequisites..."
    printf "\n"
    check_os
    check_git
    check_python
    check_uv
    check_gh
    printf "\n"

    # ── Clone ──
    if [ -d "$GAAS_HOME/.git" ]; then
        info "Repository already exists at $GAAS_HOME, updating..."
        (cd "$GAAS_HOME" && git pull --ff-only origin "$GAAS_BRANCH")
        success "Repository updated"
    else
        if [ -d "$GAAS_HOME" ] && [ "$(ls -A "$GAAS_HOME" 2>/dev/null)" ]; then
            fatal "$GAAS_HOME exists and is not empty. Remove it or set GAAS_HOME to another path."
        fi
        clone_repo
    fi

    # ── Dependencies ──
    install_deps

    # ── Wrapper + PATH ──
    write_wrapper
    add_to_path

    # ── Done ──
    printf "\n"
    printf "%b%b  GaaS installed successfully!%b\n" "${GREEN}" "${BOLD}" "${NC}"
    printf "\n"
    info "Run %bgaas setup%b to configure your installation." "${BOLD}" "${NC}"
    info "Run %bgaas doctor%b to verify everything is working." "${BOLD}" "${NC}"
    info "Run %bgaas start%b  to start the server." "${BOLD}" "${NC}"
    printf "\n"

    # Offer to run setup now, reconnecting stdin to the terminal
    if [ -t 1 ]; then
        info "Would you like to run the setup wizard now? [Y/n] "
        local answer
        if [ -t 0 ]; then
            read -r answer
        else
            read -r answer < /dev/tty 2>/dev/null || answer="y"
        fi
        if [ "$answer" != "n" ] && [ "$answer" != "N" ]; then
            printf "\n"
            if [ -t 0 ]; then
                exec "$WRAPPER" setup
            else
                exec "$WRAPPER" setup < /dev/tty
            fi
        fi
    fi
}

main "$@"
