echo "Running all ubmarks."
/usr/bin/firesim-start-trigger
./root/ubmarks/run-all.sh

# we don't write to filesystem, so this is fine
poweroff -f
