package io.github.tinmachine.eidolon

/** Activity callbacks have no authority until their own Scene is admitted. */
internal class ActivitySceneOwnership {
    private var owner = 0L
    private var epoch = 0L
    @Synchronized fun register(): Long {
        owner = Math.addExact(owner, 1)
        active = 0
        return owner
    }
    @Synchronized fun currentOwner(value: Long): Boolean = value > 0 && value == owner
    @Synchronized fun admit(value: Long, scene: Long): Boolean {
        if (!currentOwner(value) || scene <= epoch) return false
        epoch = scene
        return true
    }
    @Synchronized fun owns(value: Long, scene: Long): Boolean =
        currentOwner(value) && scene > 0 && scene == epoch && scene == active
    private var active = 0L
    @Synchronized fun activate(value: Long, scene: Long): Boolean {
        if (!admit(value, scene)) return false
        active = scene
        return true
    }
    @Synchronized fun close(value: Long, scene: Long): Boolean {
        if (!owns(value, scene)) return false
        active = 0
        return true
    }
    @Synchronized fun activeEpoch(): Long = active
}
