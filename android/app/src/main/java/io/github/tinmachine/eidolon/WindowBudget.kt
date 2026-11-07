package io.github.tinmachine.eidolon

/** 通常窓の表示/SDK所有予算。nativeの予約台帳が受付・回収の正本。 */
object WindowBudget {
    const val MAX_WINDOWS = 9
    private val positions = listOf("中央","右","左","上","下","左上","右上","左下","右下")
    fun position(slot: Int?): String = slot?.let {positions.getOrNull(it)} ?: "準備中"
}
