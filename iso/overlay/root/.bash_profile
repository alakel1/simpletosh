# Live ISO: start the installer automatically on the first console only.
if [ "$(tty)" = "/dev/tty1" ] && [ -z "${OCL_SESSION:-}" ]; then
    export OCL_SESSION=1
    /usr/local/bin/oclauncher-session
    echo
    echo "OpenCore Launcher closed."
    echo "  oclauncher-session   start it again"
    echo "  nmtui                configure the network"
    echo "  reboot / poweroff"
fi
