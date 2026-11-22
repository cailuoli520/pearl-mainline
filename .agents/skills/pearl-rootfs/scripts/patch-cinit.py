#!/usr/bin/env python3
"""Apply the scoped Pearl dual-ACM integration to the cloned, no-libc AArch64 CINIT."""
from pathlib import Path
import sys

SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "inputs/cinit-source/init.c"

# The handoff must never depend on being able to kill the early USB helper: on
# real hardware a child can stay unreapable, and aborting there costs the desktop.
NEW_HANDOFF = r'''/* A PID that still answers kill(pid,0) may merely be an unreaped zombie, so the
 * authoritative question is whether /proc/<pid> still exists. */
static int pid_present(long pid)
{
    char path[64], value[24];
    long fd;
    number_(value, pid);
    concat(path, "/proc/", value, sizeof(path));
    fd = openat(AT_FDCWD, path, O_RDONLY | O_DIRECTORY, 0);
    if (fd < 0) return 0;
    close_(fd);
    return 1;
}

static int live_pids(int count)
{
    int i, live = 0;
    for (i = 0; i < count; ++i) if (pid_present(g_stop_pids[i])) ++live;
    return live;
}

/* Log every survivor by pid and comm so the next iteration has real names. */
static int log_survivors(int count)
{
    int i, live = 0;
    for (i = 0; i < count; ++i) {
        char path[64], value[24], record[128], message[224];
        long fd, n, pid = g_stop_pids[i];
        if (!pid_present(pid)) continue;
        ++live;
        number_(value, pid);
        concat(path, "/proc/", value, sizeof(path));
        concat(path, path, "/comm", sizeof(path));
        record[0] = 0;
        fd = openat(AT_FDCWD, path, O_RDONLY, 0);
        if (fd >= 0) {
            n = read_(fd, record, sizeof(record) - 1);
            close_(fd);
            if (n > 0) {
                record[n] = 0;
                while (n > 0 && (record[n - 1] == '\n' || record[n - 1] == '\r')) record[--n] = 0;
            } else {
                record[0] = 0;
            }
        }
        concat(message, "CINIT: early child survived pid=", value, sizeof(message));
        concat(message, message, " comm=", sizeof(message));
        concat(message, message, record[0] ? record : "?", sizeof(message));
        concat(message, message, "\n", sizeof(message));
        kmsg(message);
    }
    return live;
}

/* Best effort only. A stray helper must never cost the desktop: after the
 * bounded TERM/KILL sweeps the handoff continues and the real init owns the box. */
static int stop_early_usb(void)
{
    int i, j, count, live;
    long helper = g_usb_pid;
    kmsg("CINIT: stopping early USB helper and all setsid descendants\n");
    sleep_ms(100); /* final boot records can be drained, independent of host attachment */
    count = remember_pids(0);
    if (helper > 1) { kill_(helper, 15); kill_(-helper, 15); }
    for (i = 0; i < 20; ++i) {
        reap_usb();
        count = remember_pids(count);
        if (!live_pids(count)) break;
        sleep_ms(100);
    }
    for (i = 0; i < 20; ++i) {
        reap_usb();
        count = remember_pids(count);
        live = live_pids(count);
        if (!live) break;
        for (j = 0; j < count; ++j) {
            kill_(g_stop_pids[j], 9);
            kill_(-g_stop_pids[j], 9);
        }
        if (helper > 1) { kill_(helper, 9); kill_(-helper, 9); }
        sleep_ms(100);
    }
    for (i = 0; i < 20; ++i) {
        reap_usb();
        if (!live_pids(count)) break;
        sleep_ms(50);
    }
    reap_usb();
    live = log_survivors(count);
    if (live) kmsg("CINIT: continuing root handoff despite unreaped early children\n");
    else kmsg("CINIT: early USB descendants stopped and reaped\n");
    g_usb_pid = 0;
    return 0;
}

'''


def fix_handoff(text):
    """Replace the aborting stop_early_usb with the non-fatal handoff version."""
    if "early child survived pid=" in text:
        return text
    start = text.find("static int live_pids(int count)\n{")
    end = text.find("static const char *const OLD_MOUNTS[] = {")
    if start < 0 or end < start:
        return text
    return text[:start] + NEW_HANDOFF + text[end:]


s = SOURCE.read_text()
if "/* PEARL USB CINIT integration */" in s:
    fixed = s.replace("#define O_DIRECTORY 0x10000", "#define O_DIRECTORY 0x4000")
    if "#define O_NOFOLLOW " not in fixed:
        fixed = fixed.replace("#define O_DIRECTORY 0x4000", "#define O_DIRECTORY 0x4000\n#define O_NOFOLLOW 0x8000", 1)
    fixed = fixed.replace("O_RDWR | O_CLOEXEC | 0x20000", "O_RDWR | O_CLOEXEC | O_NOFOLLOW")
    fixed = fix_handoff(fixed)
    tmp_fixed = fixed.replace('mount_("tmpfs", "/newroot/tmp", "tmpfs", MS_NOSUID | MS_NOEXEC, 0)', 'mount_("tmpfs", "/newroot/tmp", "tmpfs", MS_NOSUID, 0)')
    if tmp_fixed != fixed:
        tmp_backup = SOURCE.with_name("init.c.before-tmp-exec")
        if not tmp_backup.exists():
            tmp_backup.write_bytes(SOURCE.read_bytes())
        fixed = tmp_fixed
    run_fixed = fixed.replace('mount_("tmpfs", "/run", "tmpfs", MS_NOSUID | MS_NOEXEC, "mode=0755")', 'mount_("tmpfs", "/run", "tmpfs", MS_NOSUID, "mode=0755")')
    if run_fixed != fixed:
        run_backup = SOURCE.with_name("init.c.before-run-exec")
        if not run_backup.exists():
            run_backup.write_bytes(SOURCE.read_bytes())
        fixed = run_fixed
    if fixed != s:
        abi_backup = SOURCE.with_name("init.c.before-arm64-open-flags")
        if not abi_backup.exists():
            abi_backup.write_bytes(SOURCE.read_bytes())
        handoff_backup = SOURCE.with_name("init.c.before-handoff-fix")
        if not handoff_backup.exists():
            handoff_backup.write_bytes(SOURCE.read_bytes())
        SOURCE.write_text(fixed)
        print(f"updated AArch64 open flags, non-fatal root handoff and /tmp + /run exec: {SOURCE}")
    else:
        print(f"already patched: {SOURCE}")
    raise SystemExit(0)
backup = SOURCE.with_name("init.c.original")
if not backup.exists():
    backup.write_bytes(SOURCE.read_bytes())
s = s.replace("getdents64=217", "getdents64=61")
s = s.replace("ksys(217, fd,", "ksys(61, fd,")
# Linux AArch64 asm/fcntl.h values; 0x10000 is O_DIRECT, not O_DIRECTORY.
s = s.replace("#define O_NONBLOCK 0x800", "#define O_NONBLOCK 0x800\n#define O_RDWR 2\n#define O_DIRECTORY 0x4000\n#define O_NOFOLLOW 0x8000\n#define O_CLOEXEC 0x80000\n#define MS_REC 0x4000\n#define MS_PRIVATE 0x40000")
s = s.replace("static long strlen_(const char *s)", r"""/* AArch64 clone(SIGCHLD) is fork-like: no shared memory and no child stack. */
static long clone_(void) { return ksys(220, 17, 0, 0, 0, 0); }
static long kill_(long pid, long sig) { return ksys(129, pid, sig, 0, 0, 0); }
static long wait4_(long pid, int *status) { return ksys(260, pid, (long)status, 1, 0, 0); }
static long setpgid_(long pid, long group) { return ksys(154, pid, group, 0, 0, 0); }
static long fchdir_(long fd) { return ksys(50, fd, 0, 0, 0, 0); }

static long strlen_(const char *s)""")
s = s.replace("static void sleep_ms(long ms)", r"""static void number_(char *out, long value)
{
    char reverse[24];
    int n = 0, i = 0;
    if (value < 0) { out[i++] = '-'; value = -value; }
    do { reverse[n++] = '0' + value % 10; value /= 10; } while (value);
    while (n) out[i++] = reverse[--n];
    out[i] = 0;
}

static void result_(const char *stage, long rc)
{
    char message[224], value[24];
    concat(message, "CINIT: ", stage, sizeof(message));
    if (rc < 0) {
        concat(message, message, " FAILED errno=", sizeof(message));
        number_(value, -rc);
        concat(message, message, value, sizeof(message));
    } else {
        concat(message, message, " OK", sizeof(message));
    }
    concat(message, message, "\n", sizeof(message));
    kmsg(message);
}

static void sleep_ms(long ms)""")
s = s.replace("    close_(fd_in);\n    close_(fd_out);\n    return 0;\n}", "    close_(fd_in);\n    close_(fd_out);\n    return got < 0 ? -1 : 0;\n}", 1)
s = s.replace("static void copy_wifi_from_nvdata(void)\n{", "static void copy_wifi_from_nvdata(void)\n{\n    long rc;")
s = s.replace('    if (mount_(NVDATA_PARTITION, "/nvdata", "ext4", MS_RDONLY, 0) != 0) {', '    rc = mount_(NVDATA_PARTITION, "/nvdata", "ext4", MS_RDONLY, 0);\n    result_("mount nvdata read-only", rc);\n    if (rc != 0) {')
s = s.replace('    umount2_("/nvdata", 0);', '    result_("unmount nvdata", umount2_("/nvdata", 0));')
a = s.index("static void list_disks(void)")
z = s.index("/* Mount BOOT_PARTITION", a)
listing = s[a:z]
listing = listing.replace("    int i, count = 0;", "    int count = 0;")
a0 = listing.index("    for (i = 0;")
a1 = listing.index('    kmsg("CINIT: disks:', a0)
listing = listing[:a0] + "    /* Root mounting below owns the 60 x 500ms device/retry budget. */\n\n" + listing[a1:]
listing = listing.replace('    close_(fd);\n\n    if (count == 0)', '    close_(fd);\n    if (got < 0) result_("getdents64 /dev", got);\n\n    if (count == 0)')
s = s[:a] + listing + s[z:]
start = s.index("/* Mount BOOT_PARTITION")
s = s[:start] + r"""/* PEARL USB CINIT integration */
static long g_usb_pid;

static void reap_usb(void)
{
    int status;
    long pid;
    while ((pid = wait4_(-1, &status)) > 0) {
        if (pid == g_usb_pid) {
            g_usb_pid = 0;
            kmsg("CINIT: USB helper exited; rescue will restart it\n");
        }
    }
}

static void start_usb(int root_mode)
{
    const char *argv[10], *envp[5];
    long pid = clone_();
    if (pid < 0) { result_("clone USB helper", pid); return; }
    if (pid == 0) {
        setpgid_(0, 0);
        argv[0] = root_mode ? "/usr/lib/pearl-usb/usb-helper" : "/bin/pearl-usb";
        argv[1] = root_mode ? "root" : "early";
        if (root_mode) {
            argv[2] = "--rescue";
            argv[3] = "--busybox";
            argv[4] = "/usr/lib/pearl-usb/busybox";
            argv[5] = "--rescue-shell";
            argv[6] = "/usr/lib/pearl-usb/sh";
            argv[7] = 0;
        } else {
            argv[2] = "--busybox";
            argv[3] = "/bin/busybox";
            argv[4] = 0;
        }
        envp[0] = "HOME=/";
        envp[1] = "TERM=vt100";
        envp[2] = "PATH=/usr/bin:/usr/sbin:/bin:/sbin";
        envp[3] = 0;
        result_("exec USB helper", execve_(argv[0], argv, envp));
        ksys(93, 127, 0, 0, 0, 0);
        for (;;) { }
    }
    g_usb_pid = pid;
    setpgid_(pid, pid);
    result_(root_mode ? "root rescue helper cloned (PID1 does not wait)" :
                        "early helper cloned (PID1 does not wait for USB)", 0);
}

static void rescue_loop(int root_mode)
{
    kmsg(root_mode ? "CINIT: staying PID1 with root BusyBox USB rescue\n" :
                     "CINIT: staying PID1 with early USB rescue\n");
    for (;;) {
        reap_usb();
        if (!g_usb_pid) start_usb(root_mode);
        sleep_ms(1000);
    }
}

#define PROC_MAX 512
struct proc_ref { long pid, parent; int child; };
static struct proc_ref g_procs[PROC_MAX];
static long g_stop_pids[PROC_MAX];

static long decimal_(const char *p)
{
    long n = 0;
    while (*p >= '0' && *p <= '9') n = n * 10 + *p++ - '0';
    return n;
}

/* PPid ancestry catches getty/login/shell sessions after setsid, plus reparented
 * children of this still-exclusive boot PID1. Kernel threads (PPid 0/2) are excluded. */
static int collect_usb_pids(long *out)
{
    unsigned char entries[4096];
    char path[96], record[256];
    long fd, got, offset;
    int count = 0, total = 0, pass, i, j;
    fd = openat(AT_FDCWD, "/proc", O_RDONLY | O_DIRECTORY, 0);
    if (fd < 0) return 0;
    while (count < PROC_MAX && (got = getdents64(fd, entries, sizeof(entries))) > 0) {
        for (offset = 0; offset < got && count < PROC_MAX;) {
            struct dirent64 *entry = (struct dirent64 *)(entries + offset);
            long proc_fd, n, pid, parent;
            char *p, *last = 0;
            if (!entry->d_reclen || offset + entry->d_reclen > got) break;
            offset += entry->d_reclen;
            if (entry->d_name[0] < '0' || entry->d_name[0] > '9') continue;
            pid = decimal_(entry->d_name);
            if (pid <= 1) continue;
            concat(path, "/proc/", entry->d_name, sizeof(path));
            concat(path, path, "/stat", sizeof(path));
            proc_fd = openat(AT_FDCWD, path, O_RDONLY, 0);
            if (proc_fd < 0) continue;
            n = read_(proc_fd, record, sizeof(record) - 1);
            close_(proc_fd);
            if (n <= 0) continue;
            record[n] = 0;
            for (p = record; *p; ++p) if (*p == ')') last = p;
            if (!last) continue;
            p = last + 1;
            while (*p == ' ') ++p;
            if (!*p) continue;
            ++p; /* process state */
            while (*p == ' ') ++p;
            parent = decimal_(p);
            g_procs[count].pid = pid;
            g_procs[count].parent = parent;
            g_procs[count++].child = parent == 1 || parent == g_usb_pid || pid == g_usb_pid;
        }
    }
    close_(fd);
    for (pass = 0; pass < count; ++pass) {
        int changed = 0;
        for (i = 0; i < count; ++i) {
            if (g_procs[i].child) continue;
            for (j = 0; j < count; ++j)
                if (g_procs[j].child && g_procs[i].parent == g_procs[j].pid) {
                    g_procs[i].child = 1;
                    changed = 1;
                    break;
                }
        }
        if (!changed) break;
    }
    for (i = 0; i < count; ++i)
        if (g_procs[i].child) out[total++] = g_procs[i].pid;
    return total;
}

static int remember_pids(int count)
{
    long fresh[PROC_MAX];
    int n = collect_usb_pids(fresh), i, j;
    for (i = 0; i < n && count < PROC_MAX; ++i) {
        for (j = 0; j < count && g_stop_pids[j] != fresh[i]; ++j) { }
        if (j == count) g_stop_pids[count++] = fresh[i];
    }
    return count;
}

static int live_pids(int count)
{
    int i, live = 0;
    for (i = 0; i < count; ++i) if (kill_(g_stop_pids[i], 0) == 0) ++live;
    return live;
}

static int stop_early_usb(void)
{
    int i, j, count;
    long helper = g_usb_pid;
    kmsg("CINIT: stopping early USB helper and all setsid descendants\n");
    sleep_ms(100); /* final boot records can be drained, independent of host attachment */
    count = remember_pids(0);
    if (helper > 1) kill_(helper, 15);
    for (i = 0; i < 20; ++i) {
        reap_usb();
        count = remember_pids(count);
        if (!live_pids(count)) break;
        sleep_ms(100);
    }
    for (i = 0; i < 10; ++i) {
        reap_usb();
        count = remember_pids(count);
        if (!live_pids(count)) break;
        for (j = 0; j < count; ++j) {
            kill_(g_stop_pids[j], 9);
            kill_(-g_stop_pids[j], 9);
        }
        if (helper > 1) { kill_(helper, 9); kill_(-helper, 9); }
        sleep_ms(50);
    }
    reap_usb();
    if (live_pids(count)) {
        kmsg("CINIT: early children remain after bounded TERM/KILL; root handoff aborted\n");
        return -1;
    }
    g_usb_pid = 0;
    kmsg("CINIT: early USB descendants stopped and reaped\n");
    return 0;
}

static const char *const OLD_MOUNTS[] = {"/run", "/sys", "/proc", "/dev"};
static const char *const NEW_MOUNTS[] = {"/newroot/run", "/newroot/sys", "/newroot/proc", "/newroot/dev"};

static void rollback_mounts(int count)
{
    while (count) {
        --count;
        result_("rollback pseudo-filesystem mount",
                mount_(NEW_MOUNTS[count], OLD_MOUNTS[count], 0, MS_MOVE, 0));
    }
}

/* Keep PID1; failed mount/chdir/chroot/exec never masquerades as systemd startup. */
static void boot_rootfs(void)
{
    const char *argv[2], *envp[4];
    long rc = -1, old_root_fd, new_root_fd;
    int attempt, moved;
    char stage[176], value[24];
    mkdir_p("/newroot");
    kmsg("CINIT: root device/mount retries: 60 attempts, 500ms interval\n");
    for (attempt = 0; attempt < 60; ++attempt) {
        rc = mount_(BOOT_PARTITION, "/newroot", "ext4", 0, 0);
        concat(stage, "mount root ", BOOT_PARTITION, sizeof(stage));
        concat(stage, stage, " attempt=", sizeof(stage));
        number_(value, attempt + 1);
        concat(stage, stage, value, sizeof(stage));
        result_(stage, rc);
        if (rc == 0) break;
        reap_usb();
        if (!g_usb_pid) start_usb(0);
        sleep_ms(500);
    }
    if (rc != 0) rescue_loop(0);

    copy_wifi_from_nvdata();
    copy_firmware_to_rootfs();
    if (!exists("/newroot/sbin/init")) {
        kmsg("CINIT: /newroot/sbin/init missing/unreadable; retaining early rescue\n");
        rescue_loop(0);
    }
    mkdir_p("/newroot/run");
    mkdir_p("/newroot/sys");
    mkdir_p("/newroot/proc");
    mkdir_p("/newroot/dev");
    mkdir_p("/newroot/tmp");
    mkdir_p("/newroot/var/log/pearl-usb");
    rc = mount_("tmpfs", "/newroot/tmp", "tmpfs", MS_NOSUID, 0);
    result_("mount root tmp", rc);
    if (rc != 0) rescue_loop(0);
    rc = mount_(0, "/", 0, MS_REC | MS_PRIVATE, 0);
    result_("make mount tree private for MS_MOVE", rc);
    if (rc != 0) rescue_loop(0);

    old_root_fd = openat(AT_FDCWD, "/", O_RDONLY | O_DIRECTORY | O_CLOEXEC, 0);
    new_root_fd = openat(AT_FDCWD, "/newroot", O_RDONLY | O_DIRECTORY | O_CLOEXEC, 0);
    if (old_root_fd < 0 || new_root_fd < 0) {
        result_("open root directory handles", old_root_fd < 0 ? old_root_fd : new_root_fd);
        if (old_root_fd >= 0) close_(old_root_fd);
        if (new_root_fd >= 0) close_(new_root_fd);
        rescue_loop(0);
    }
    result_("open root directory handles", 0);
    if (stop_early_usb()) { close_(old_root_fd); close_(new_root_fd); rescue_loop(0); }
    rc = copy_file("/pearl-early.log", "/newroot/var/log/pearl-usb/initramfs.log");
    result_("save initramfs log after helper shutdown", rc);

    for (moved = 0; moved < 4; ++moved) {
        rc = mount_(OLD_MOUNTS[moved], NEW_MOUNTS[moved], 0, MS_MOVE, 0);
        concat(stage, "move ", OLD_MOUNTS[moved], sizeof(stage));
        result_(stage, rc);
        if (rc != 0) {
            rollback_mounts(moved);
            close_(old_root_fd); close_(new_root_fd);
            rescue_loop(0);
        }
    }
    /* Moving /sys carries configfs and the still-bound gadget; moving /dev carries devpts. */
    rc = fchdir_(new_root_fd);
    result_("fchdir newroot", rc);
    if (rc != 0) {
        rollback_mounts(moved);
        close_(old_root_fd); close_(new_root_fd);
        rescue_loop(0);
    }
    rc = mount_(".", "/", 0, MS_MOVE, 0);
    result_("MS_MOVE real root over initramfs", rc);
    if (rc != 0) {
        result_("restore old cwd", fchdir_(old_root_fd));
        rollback_mounts(moved);
        close_(old_root_fd); close_(new_root_fd);
        rescue_loop(0);
    }
    rc = chroot_(".");
    result_("chroot real root", rc);
    if (rc != 0) {
        /* The still-open old-root fd permits a best-effort reverse move before chroot. */
        long back = mount_(".", "/newroot", 0, MS_MOVE, 0);
        result_("rollback real-root move after chroot error", back);
        if (back == 0) {
            result_("restore old cwd", fchdir_(old_root_fd));
            rollback_mounts(moved);
            close_(old_root_fd); close_(new_root_fd);
            rescue_loop(0);
        }
        close_(old_root_fd); close_(new_root_fd);
        rescue_loop(1);
    }
    rc = chdir_("/");
    result_("chdir / in real root", rc);
    close_(old_root_fd);
    close_(new_root_fd);
    if (rc != 0) rescue_loop(1);

    argv[0] = "/sbin/init";
    argv[1] = 0;
    envp[0] = "HOME=/";
    envp[1] = "TERM=linux";
    envp[2] = "PATH=/usr/bin:/usr/sbin:/bin:/sbin";
    envp[3] = 0;
    kmsg("CINIT: exec /sbin/init as PID1; early descendants are gone\n");
    rc = execve_("/sbin/init", argv, envp);
    result_("exec /sbin/init", rc);
    rescue_loop(1);
}

static void console_stdio(void)
{
    long fd = openat(AT_FDCWD, "/dev/console", O_RDWR | O_NONBLOCK, 0);
    int i;
    if (fd < 0) return;
    for (i = 0; i < 3; ++i) if (fd != i) ksys(24, fd, i, 0, 0, 0);
    if (fd > 2) close_(fd);
}

void _start(void)
{
    long dev_rc, proc_rc, sys_rc, run_rc, pts_rc;
    mkdirat(AT_FDCWD, "/dev", 0755);
    dev_rc = mount_("devtmpfs", "/dev", "devtmpfs", MS_NOSUID | MS_STRICTATIME, "mode=0755");
    console_stdio();
    g_kmsg_fd = openat(AT_FDCWD, "/dev/kmsg", O_WRONLY | O_NONBLOCK | O_CLOEXEC, 0);
    if (g_kmsg_fd < 0) g_kmsg_fd = 2;
    kmsg("CINIT: Pearl USB init start (native AArch64)\n");
    result_("mount early devtmpfs", dev_rc);
    mkdirat(AT_FDCWD, "/proc", 0755);
    proc_rc = mount_("proc", "/proc", "proc", MS_NOSUID | MS_NOEXEC, 0);
    result_("mount early proc", proc_rc);
    mkdirat(AT_FDCWD, "/sys", 0755);
    sys_rc = mount_("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NOEXEC, 0);
    result_("mount early sysfs", sys_rc);
    mkdirat(AT_FDCWD, "/run", 0755);
    run_rc = mount_("tmpfs", "/run", "tmpfs", MS_NOSUID, "mode=0755");
    result_("mount early run", run_rc);
    mkdirat(AT_FDCWD, "/dev/pts", 0755);
    pts_rc = mount_("devpts", "/dev/pts", "devpts", MS_NOSUID | MS_NOEXEC, "mode=0620,ptmxmode=0666");
    result_("mount early devpts", pts_rc);

    start_usb(0); /* PID1 continues immediately: all USB waiting is in the clone. */
    list_disks();
    if (dev_rc != 0 || proc_rc != 0 || sys_rc != 0 || run_rc != 0) rescue_loop(0);
    boot_rootfs();
    rescue_loop(0);
}
"""
s = fix_handoff(s)
SOURCE.write_text(s)
print(f"patched {SOURCE}: {len(s.encode())} bytes; original retained at {backup}")
