/* Pearl USB: no device operation is performed by the build; run early/root on-device. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define CFG "/sys/kernel/config"
#define GROOT CFG "/usb_gadget"
#define GNAME "pearl_usb"
#define GADGET GROOT "/" GNAME
#define RING_SIZE (256u * 1024u)
#define MAX_PROCS 1024

static volatile sig_atomic_t stopping;
static bool early, rescue, bound, configured, setup_finished, configured_once;
static int disk_fd = -1, kmsg_fd = -1, journal_fd = -1, log_fd = -1;
static int lock_fd = -1;
static pid_t getty_pid, journal_pid;
static uint64_t getty_started;
static int getty_fast_exits;
static const char *busybox = "/bin/busybox", *agetty = "/usr/bin/agetty";
static const char *rescue_shell = "/usr/lib/pearl-usb/sh";
static char udc[128], log_tty[64], shell_tty[64], shell_name[32], pid_path[128];
static unsigned char ring[RING_SIZE];
static uint64_t ring_first, ring_end, send_pos;
static bool ring_previous_cr, log_connected;
static uint64_t next_getty, next_journal, next_setup, setup_deadline;

static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void on_stop(int sig) { (void)sig; stopping = 1; }

static int write_all(int fd, const void *data, size_t length)
{
    const unsigned char *p = data;
    while (length) {
        ssize_t n = write(fd, p, length);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        p += n;
        length -= (size_t)n;
    }
    return 0;
}

static void ring_byte(unsigned char c)
{
    ring[ring_end++ % RING_SIZE] = c;
    if (ring_end - ring_first > RING_SIZE) ring_first = ring_end - RING_SIZE;
}

static void remember(const void *data, size_t length)
{
    const unsigned char *p = data;
    for (size_t i = 0; i < length; ++i) {
        if (p[i] == '\n' && !ring_previous_cr) ring_byte('\r');
        ring_byte(p[i]);
        ring_previous_cr = p[i] == '\r';
    }
}

static void emit_data(const void *data, size_t length)
{
    if (disk_fd >= 0 && write_all(disk_fd, data, length) < 0) {
        close(disk_fd);
        disk_fd = -1;
    }
    remember(data, length);
}

static void note(const char *format, ...)
{
    char data[1024];
    va_list ap;
    va_start(ap, format);
    int n = vsnprintf(data, sizeof(data), format, ap);
    va_end(ap);
    if (n < 0) return;
    size_t length = (size_t)n < sizeof(data) ? (size_t)n : sizeof(data) - 1;
    emit_data(data, length);
}

static int mkdir_p(const char *path, mode_t mode)
{
    char copy[PATH_MAX];
    if (strlen(path) >= sizeof(copy)) { errno = ENAMETOOLONG; return -1; }
    strcpy(copy, path);
    for (char *p = copy + 1; *p; ++p) {
        if (*p != '/') continue;
        *p = 0;
        if (mkdir(copy, mode) && errno != EEXIST) return -1;
        *p = '/';
    }
    if (mkdir(copy, mode) && errno != EEXIST) return -1;
    return 0;
}

static int attr_read(const char *path, char *value, size_t capacity)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t n;
    do { n = read(fd, value, capacity - 1); } while (n < 0 && errno == EINTR);
    int err = errno;
    close(fd);
    errno = err;
    if (n < 0) return -1;
    while (n && (value[n - 1] == '\n' || value[n - 1] == '\r' || value[n - 1] == ' ')) --n;
    value[n] = 0;
    return 0;
}

static int attr_write(const char *path, const char *value)
{
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    int rc = write_all(fd, value, strlen(value));
    int err = errno;
    close(fd);
    errno = err;
    return rc;
}

static int make_link(const char *target, const char *path)
{
    /* configfs resolves symname with kern_path at syscall time, relative to cwd.
     * Always pass an absolute target; readlink text is normalized by configfs. */
    if (symlink(target, path) == 0) return 0;
    if (errno != EEXIST) return -1;
    struct stat actual, wanted;
    if (stat(path, &actual) || stat(target, &wanted)) return -1;
    if (actual.st_dev == wanted.st_dev && actual.st_ino == wanted.st_ino) return 0;
    errno = EEXIST;
    return -1;
}

static void drain_kmsg(void)
{
    if (kmsg_fd < 0) return;
    char data[16384];
    for (unsigned i = 0; i < 64; ++i) {
        ssize_t n = read(kmsg_fd, data, sizeof(data));
        if (n > 0) { emit_data(data, (size_t)n); continue; }
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && errno == EPIPE) {
            note("[pearl-usb] kernel ring overrun; resuming available records\n");
            continue;
        }
        break;
    }
}

/* Only touch another gadget if it is actually bound to the chosen UDC; once. */
static int release_udc_owner(void)
{
    static bool attempted;
    if (attempted) return 0;
    DIR *dir = opendir(GROOT);
    if (!dir) return -1;
    attempted = true;
    struct dirent *entry;
    int rc = 0;
    while ((entry = readdir(dir))) {
        if (entry->d_name[0] == '.' || !strcmp(entry->d_name, GNAME)) continue;
        char path[PATH_MAX], value[128];
        if (snprintf(path, sizeof(path), "%s/%s/UDC", GROOT, entry->d_name) >= (int)sizeof(path)) continue;
        if (attr_read(path, value, sizeof(value)) || strcmp(value, udc)) continue;
        if (attr_write(path, "\n")) {
            note("[pearl-usb] unbind gadget %s from %s failed: %s\n", entry->d_name, udc, strerror(errno));
            rc = -1;
            break;
        }
        note("[pearl-usb] released existing gadget %s on %s\n", entry->d_name, udc);
    }
    closedir(dir);
    return rc;
}

static int choose_udc(void)
{
    DIR *dir = opendir("/sys/class/udc");
    if (!dir) return -1;
    char ours[128] = {0};
    (void)attr_read(GADGET "/UDC", ours, sizeof(ours));
    struct dirent *entry;
    char candidate[128] = {0};
    while ((entry = readdir(dir))) {
        if (entry->d_name[0] == '.' || strlen(entry->d_name) >= sizeof(candidate)) continue;
        if (*ours && !strcmp(entry->d_name, ours)) {
            strcpy(candidate, ours);
            break;
        }
        if (!*candidate || strcmp(entry->d_name, candidate) < 0) strcpy(candidate, entry->d_name);
    }
    closedir(dir);
    if (!*candidate) { errno = ENODEV; return -1; }
    strcpy(udc, candidate);
    return 0;
}

static int read_ports(void)
{
    char value[32], *end;
    if (attr_read(GADGET "/functions/acm.log/port_num", value, sizeof(value))) return -1;
    long a = strtol(value, &end, 10);
    if (*end || a < 0 || a > 4095) { errno = EINVAL; return -1; }
    if (attr_read(GADGET "/functions/acm.shell/port_num", value, sizeof(value))) return -1;
    long b = strtol(value, &end, 10);
    if (*end || b < 0 || b > 4095 || a == b) { errno = EINVAL; return -1; }
    snprintf(log_tty, sizeof(log_tty), "/dev/ttyGS%ld", a);
    snprintf(shell_tty, sizeof(shell_tty), "/dev/ttyGS%ld", b);
    snprintf(shell_name, sizeof(shell_name), "ttyGS%ld", b);
    note("[pearl-usb] ACM port_num: log=%ld (%s), shell=%ld (%s)\n", a, log_tty, b, shell_tty);
    return 0;
}

static int prepare_gadget(void)
{
    if (mkdir_p(CFG, 0755)) return -1;
    if (access(GROOT, F_OK) && mount("configfs", CFG, "configfs", MS_NOSUID | MS_NOEXEC, NULL)) {
        if (errno != EBUSY || access(GROOT, F_OK)) return -1;
    }
    if (mkdir_p(GADGET, 0755)) return -1;
    char value[128];
    if (!attr_read(GADGET "/UDC", value, sizeof(value)) && *value) {
        if (strcmp(value, udc)) { errno = EBUSY; return -1; }
        if (read_ports()) return -1;
        bound = true;
        note("[pearl-usb] reused bound gadget on %s\n", udc);
        return 0;
    }
#define DIRECTORY(s) do { if (mkdir_p(GADGET s, 0755)) return -1; } while (0)
#define ATTRIBUTE(s,v) do { if (attr_write(GADGET s, v)) { note("[pearl-usb] configfs %s failed: %s\n", s, strerror(errno)); return -1; } } while (0)
    ATTRIBUTE("/idVendor", "0x1d6b\n");
    ATTRIBUTE("/idProduct", "0x0104\n");
    ATTRIBUTE("/bcdUSB", "0x0200\n");
    ATTRIBUTE("/bcdDevice", "0x0100\n");
    ATTRIBUTE("/bDeviceClass", "0xEF\n");
    ATTRIBUTE("/bDeviceSubClass", "0x02\n");
    ATTRIBUTE("/bDeviceProtocol", "0x01\n");
    DIRECTORY("/strings/0x409");
    ATTRIBUTE("/strings/0x409/serialnumber", "PEARL-KDE-USB\n");
    ATTRIBUTE("/strings/0x409/manufacturer", "Pearl\n");
    ATTRIBUTE("/strings/0x409/product", "Pearl USB Log + Shell\n");
    DIRECTORY("/configs/c.1");
    ATTRIBUTE("/configs/c.1/bmAttributes", "0x80\n");
    ATTRIBUTE("/configs/c.1/MaxPower", "500\n");
    DIRECTORY("/configs/c.1/strings/0x409");
    ATTRIBUTE("/configs/c.1/strings/0x409/configuration", "Logs + Console\n");
    DIRECTORY("/functions/acm.log");
    DIRECTORY("/functions/acm.shell");
    if (make_link(GADGET "/functions/acm.log", GADGET "/configs/c.1/acm.log") ||
        make_link(GADGET "/functions/acm.shell", GADGET "/configs/c.1/acm.shell")) return -1;
#undef DIRECTORY
#undef ATTRIBUTE
    if (read_ports() || release_udc_owner()) return -1;
    if (attr_write(GADGET "/UDC", udc)) return -1;
    bound = true;
    note("[pearl-usb] bound EF/02/01 1d6b:0104 dual ACM to registered UDC %s\n", udc);
    return 0;
}

static void setup_tick(uint64_t now)
{
    static bool warned;
    static int last_error;
    if (setup_finished || stopping || now < next_setup) return;
    if (now >= setup_deadline && !warned) {
        warned = true;
        note("[pearl-usb] UDC/gadget not ready after 8s; retrying asynchronously, PID1 remains unblocked\n");
    }
    next_setup = now + 1000;
    if (choose_udc()) return;
    if (!prepare_gadget()) {
        setup_finished = true;
        return;
    }
    int error = errno;
    if (error != last_error) {
        last_error = error;
        note("[pearl-usb] gadget setup failed: %s; retrying own gadget without repeated owner release\n", strerror(error));
    }
}

static void save_pids(void)
{
    char temporary[160];
    snprintf(temporary, sizeof(temporary), "%s.tmp", pid_path);
    int fd = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) return;
    dprintf(fd, "%ld\n%ld\n%ld\n", (long)getpid(), (long)getty_pid, (long)journal_pid);
    close(fd);
    (void)rename(temporary, pid_path);
}

static void child_signals(void)
{
    signal(SIGTERM, SIG_DFL);
    signal(SIGINT, SIG_DFL);
    signal(SIGHUP, SIG_DFL);
    signal(SIGPIPE, SIG_DFL);
    pid_t parent = getppid();
    (void)prctl(PR_SET_PDEATHSIG, SIGKILL);
    if (getppid() != parent) _exit(125);
}

static void start_getty(uint64_t now)
{
    if (!bound || getty_pid || stopping || now < next_getty || access(shell_tty, F_OK)) return;
    next_getty = now + 1000;
    pid_t pid = fork();
    if (pid == 0) {
        child_signals();
        /* "-L" is the argument-free form: agetty in this image rejects
         * --local-line=<mode> and exits before printing the login prompt. */
        if (early || rescue)
            execl(busybox, busybox, "getty", "-L", "-n", "-l", early ? "/bin/sh" : rescue_shell,
                  "115200", shell_name, "vt100", (char *)NULL);
        else if (getty_fast_exits < 3)
            execl(agetty, agetty, "-L", "--noclear", shell_name,
                  "115200", "vt100", (char *)NULL);
        else
            execl(busybox, busybox, "getty", "-L", "-n", "-l", "/bin/login",
                  "115200", shell_name, "vt100", (char *)NULL);
        _exit(127);
    }
    if (pid < 0) { note("[pearl-usb] getty fork failed: %s\n", strerror(errno)); return; }
    getty_pid = pid;
    getty_started = now;
    save_pids();
    note("[pearl-usb] %s getty pid=%ld on %s; exits respawn after 1s\n",
         early || rescue ? "BusyBox rescue" : "account-login agetty", (long)pid, shell_tty);
}

static void start_journal(uint64_t now)
{
    if (early || journal_pid || stopping || now < next_journal) return;
    next_journal = now + 1000;
    int fds[2];
    if (pipe2(fds, O_CLOEXEC)) { note("[pearl-usb] journal pipe failed: %s\n", strerror(errno)); return; }
    int flags = fcntl(fds[0], F_GETFL);
    if (flags >= 0) (void)fcntl(fds[0], F_SETFL, flags | O_NONBLOCK);
    pid_t pid = fork();
    if (pid == 0) {
        child_signals();
        close(fds[0]);
        (void)dup2(fds[1], STDOUT_FILENO);
        (void)dup2(fds[1], STDERR_FILENO);
        close(fds[1]);
        int nullfd = open("/dev/null", O_RDONLY);
        if (nullfd >= 0) { (void)dup2(nullfd, STDIN_FILENO); if (nullfd != STDIN_FILENO) close(nullfd); }
        execl("/usr/bin/journalctl", "journalctl", "--no-pager", "--no-tail", "-b", "-f",
              "-o", "short-monotonic", (char *)NULL);
        _exit(127);
    }
    close(fds[1]);
    if (pid < 0) { close(fds[0]); note("[pearl-usb] journal fork failed: %s\n", strerror(errno)); return; }
    journal_fd = fds[0];
    journal_pid = pid;
    save_pids();
    note("[pearl-usb] journalctl -b -f pid=%ld\n", (long)pid);
}

static void drain_journal(void)
{
    if (journal_fd < 0) return;
    char data[8192];
    for (unsigned i = 0; i < 64; ++i) {
        ssize_t n = read(journal_fd, data, sizeof(data));
        if (n > 0) { emit_data(data, (size_t)n); continue; }
        if (n < 0 && errno == EINTR) continue;
        break;
    }
}

static void reap_children(void)
{
    int status;
    pid_t pid;
    while ((pid = waitpid(-1, &status, WNOHANG)) > 0) {
        if (pid == getty_pid) {
            uint64_t now = now_ms();
            if (now - getty_started < 2000) {
                if (getty_fast_exits < 100) ++getty_fast_exits;
                if (getty_fast_exits == 3)
                    note("[pearl-usb] agetty keeps exiting fast; switching to BusyBox getty login\n");
            } else {
                getty_fast_exits = 0;
            }
            note("[pearl-usb] getty pid=%ld exited status=%d after %llums\n",
                 (long)pid, status, (unsigned long long)(now - getty_started));
            getty_pid = 0;
            next_getty = now + 1000;
        }
        if (pid == journal_pid) {
            drain_journal();
            if (journal_fd >= 0) close(journal_fd);
            journal_fd = -1;
            note("[pearl-usb] journalctl pid=%ld exited status=%d; kernel-log fallback\n", (long)pid, status);
            journal_pid = 0;
            next_journal = now_ms() + 1000;
        }
    }
    save_pids();
}

static void log_tick(void)
{
    if (!bound) return;
    char path[256], state[64];
    snprintf(path, sizeof(path), "/sys/class/udc/%s/state", udc);
    bool state_known = attr_read(path, state, sizeof(state)) == 0;
    configured = !state_known || !strcmp(state, "configured");
    if (log_fd < 0) {
        log_fd = open(log_tty, O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
        if (log_fd < 0) return;
        struct termios term;
        if (!tcgetattr(log_fd, &term)) {
            cfmakeraw(&term);
            term.c_cflag |= CLOCAL | CREAD;
            cfsetispeed(&term, B115200);
            cfsetospeed(&term, B115200);
            (void)tcsetattr(log_fd, TCSANOW, &term);
        }
        log_connected = false;
    }
    int bits = 0;
    bool modem_known = ioctl(log_fd, TIOCMGET, &bits) == 0;
    bool connected = configured && (!modem_known || (bits & TIOCM_DTR));
    if (!connected) { log_connected = false; return; }
    if (!log_connected) {
        /* Flush stale USB TX data; replay the bounded ring on every host/DTR reconnect. */
        (void)tcflush(log_fd, TCOFLUSH);
        send_pos = ring_first;
        log_connected = true;
        configured_once = true;
    }
    if (send_pos < ring_first) send_pos = ring_first;
    size_t budget = 16384;
    while (send_pos < ring_end && budget) {
        size_t offset = (size_t)(send_pos % RING_SIZE);
        uint64_t available = ring_end - send_pos;
        size_t length = RING_SIZE - offset;
        if ((uint64_t)length > available) length = (size_t)available;
        if (length > budget) length = budget;
        ssize_t n = write(log_fd, ring + offset, length);
        if (n > 0) { send_pos += (size_t)n; budget -= (size_t)n; continue; }
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        close(log_fd);
        log_fd = -1;
        log_connected = false;
        break;
    }
}

/* PPid ancestry, not process-group membership: getty/login may call setsid(). */
struct proc_entry { pid_t pid, parent; bool child; };
static size_t descendants(pid_t *out, size_t capacity)
{
    struct proc_entry entries[MAX_PROCS];
    size_t count = 0, total = 0;
    DIR *dir = opendir("/proc");
    if (!dir) return 0;
    struct dirent *entry;
    while (count < MAX_PROCS && (entry = readdir(dir))) {
        char *end;
        long pid = strtol(entry->d_name, &end, 10);
        if (*end || pid <= 1 || pid == getpid()) continue;
        char path[64], data[512];
        snprintf(path, sizeof(path), "/proc/%ld/stat", pid);
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) continue;
        ssize_t n = read(fd, data, sizeof(data) - 1);
        close(fd);
        if (n <= 0) continue;
        data[n] = 0;
        char *p = strrchr(data, ')');
        long parent;
        char state;
        if (!p || sscanf(p + 1, " %c %ld", &state, &parent) != 2) continue;
        entries[count++] = (struct proc_entry){ (pid_t)pid, (pid_t)parent, parent == getpid() };
    }
    closedir(dir);
    for (size_t pass = 0; pass < count; ++pass) {
        bool changed = false;
        for (size_t i = 0; i < count; ++i) {
            if (entries[i].child) continue;
            for (size_t j = 0; j < count; ++j) {
                if (entries[j].child && entries[i].parent == entries[j].pid) {
                    entries[i].child = true;
                    changed = true;
                    break;
                }
            }
        }
        if (!changed) break;
    }
    for (size_t i = 0; i < count && total < capacity; ++i)
        if (entries[i].child) out[total++] = entries[i].pid;
    return total;
}

static void signal_children(int sig)
{
    pid_t pids[MAX_PROCS];
    size_t count = descendants(pids, MAX_PROCS);
    for (size_t i = count; i; --i) (void)kill(pids[i - 1], sig);
    if (getty_pid > 1) { (void)kill(getty_pid, sig); (void)kill(-getty_pid, sig); }
    if (journal_pid > 1) (void)kill(journal_pid, sig);
}

static void stop_children(void)
{
    signal_children(SIGTERM);
    uint64_t deadline = now_ms() + 1200;
    while (now_ms() < deadline) {
        reap_children();
        pid_t pids[MAX_PROCS];
        if (!descendants(pids, MAX_PROCS)) break;
        (void)poll(NULL, 0, 50);
    }
    signal_children(SIGKILL);
    deadline = now_ms() + 500;
    while (now_ms() < deadline) {
        reap_children();
        pid_t pids[MAX_PROCS];
        if (!descendants(pids, MAX_PROCS)) break;
        signal_children(SIGKILL);
        (void)poll(NULL, 0, 50);
    }
    unlink(pid_path);
    /* Keep configfs gadget bound for the root helper; no teardown/re-enumeration here. */
}

static void seed_early_log(void)
{
    int fd = open("/var/log/pearl-usb/initramfs.log", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    off_t length = lseek(fd, 0, SEEK_END);
    if (length > (off_t)RING_SIZE / 2) (void)lseek(fd, length - RING_SIZE / 2, SEEK_SET);
    else (void)lseek(fd, 0, SEEK_SET);
    char data[8192];
    ssize_t n;
    while ((n = read(fd, data, sizeof(data))) > 0) remember(data, (size_t)n);
    close(fd);
}

int main(int argc, char **argv)
{
    if (argc < 2 || (strcmp(argv[1], "early") && strcmp(argv[1], "root"))) {
        fprintf(stderr, "usage: %s early|root [--rescue] [--busybox PATH] [--agetty PATH] [--rescue-shell PATH]\n", argv[0]);
        return 2;
    }
    early = !strcmp(argv[1], "early");
    if (!early) busybox = "/usr/lib/pearl-usb/busybox";
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i], "--rescue")) { rescue = true; continue; }
        if (i + 1 < argc && !strcmp(argv[i], "--busybox")) { busybox = argv[++i]; continue; }
        if (i + 1 < argc && !strcmp(argv[i], "--agetty")) { agetty = argv[++i]; continue; }
        if (i + 1 < argc && !strcmp(argv[i], "--rescue-shell")) { rescue_shell = argv[++i]; continue; }
        return 2;
    }
    signal(SIGTERM, on_stop);
    signal(SIGINT, on_stop);
    signal(SIGHUP, on_stop);
    signal(SIGPIPE, SIG_IGN);
    (void)prctl(PR_SET_CHILD_SUBREAPER, 1);
    (void)setpgid(0, 0);
    setenv("PATH", "/usr/bin:/usr/sbin:/bin:/sbin", 1);
    setenv("HOME", "/", 1);
    setenv("TERM", "vt100", 1);
    setenv("SYSTEMD_COLORS", "0", 1);
    umask(077);
    (void)mkdir_p("/run", 0755);
    lock_fd = open("/run/pearl-usb.lock", O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (lock_fd < 0 || flock(lock_fd, LOCK_EX | LOCK_NB)) return 3;
    snprintf(pid_path, sizeof(pid_path), "/run/pearl-usb-%s.pids", early ? "early" : "root");
    if (early) {
        disk_fd = open("/pearl-early.log", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    } else {
        (void)mkdir_p("/var/log/pearl-usb", 0700);
        disk_fd = open("/var/log/pearl-usb/boot.log", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
        seed_early_log();
    }
    /* Open and drain kmsg before configfs, UDC discovery, journal or getty work. */
    kmsg_fd = open("/dev/kmsg", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (kmsg_fd >= 0) (void)lseek(kmsg_fd, 0, SEEK_SET);
    if (early || rescue) drain_kmsg();
    note("[pearl-usb] mode=%s%s pid=%ld ring=%u bytes; no USB role forcing\n",
         early ? "early" : "root", rescue ? "-rescue" : "", (long)getpid(), RING_SIZE);
    setup_deadline = now_ms() + 8000;
    save_pids();
    while (!stopping) {
        uint64_t now = now_ms();
        if (early || journal_fd < 0) drain_kmsg();
        drain_journal();
        reap_children();
        setup_tick(now);
        start_getty(now);
        start_journal(now);
        log_tick();
        struct pollfd fds[2];
        nfds_t count = 0;
        if (kmsg_fd >= 0 && (early || journal_fd < 0)) fds[count++] = (struct pollfd){kmsg_fd, POLLIN, 0};
        if (journal_fd >= 0) fds[count++] = (struct pollfd){journal_fd, POLLIN, 0};
        (void)poll(fds, count, 100);
    }
    note("[pearl-usb] stopping descendants (including setsid getty sessions)\n");
    stop_children();
    if (disk_fd >= 0) { (void)fsync(disk_fd); close(disk_fd); }
    if (kmsg_fd >= 0) close(kmsg_fd);
    if (journal_fd >= 0) close(journal_fd);
    if (log_fd >= 0) close(log_fd);
    close(lock_fd);
    (void)configured_once;
    return 0;
}
