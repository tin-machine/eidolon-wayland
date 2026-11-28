package io.github.tinmachine.eidolon

/** 通常配置を記録し、呼び寄せ先への剛体変換だけを計算する。 */
internal class SummonHome private constructor(
    val reference: InputWindowKey,
    val groupRevision: Long?,
    private val basePoses: Map<InputWindowKey, WindowMotionPose>,
) {
    private val referenceInverse = basePoses.getValue(reference).inverse()

    fun matches(targets: Set<InputWindowKey>, revision: Long?): Boolean =
        groupRevision == revision && basePoses.keys == targets

    fun returnPoses(): Map<InputWindowKey, WindowMotionPose> = basePoses.toMap()

    fun posesAt(referencePose: WindowMotionPose): Map<InputWindowKey, WindowMotionPose>? {
        if (!referencePose.valid(window = true)) return null
        val displacement = referencePose * referenceInverse
        val poses = basePoses.mapValues { (_, pose) -> displacement * pose }
        return poses.takeIf { it.values.all { pose -> pose.valid(window = true) } }
    }

    companion object {
        fun capture(
            reference: InputWindowKey,
            groupRevision: Long?,
            applied: Map<InputWindowKey, WindowMotionPose>,
        ): SummonHome? {
            if (reference !in applied || applied.keys.any { it.scene != reference.scene } ||
                applied.values.any { !it.valid(window = true) }
            ) return null
            if (groupRevision == null && applied.size != 1 ||
                groupRevision != null && (groupRevision <= 0 || applied.size < 2)
            ) return null
            return SummonHome(reference, groupRevision, applied.toMap())
        }
    }
}
