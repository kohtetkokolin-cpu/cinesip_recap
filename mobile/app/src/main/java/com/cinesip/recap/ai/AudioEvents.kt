package com.cinesip.recap.ai

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Audio event at a timestamp. */
data class AudioEvent(val start: Double, val label: String, val score: Float)

/**
 * On-device audio events — STUB for Phase 1.
 * Phase 2: TFLite YAMNet (build stabilized first).
 */
class AudioEvents(private val ctx: Context) {
    fun isAvailable(): Boolean = false
    suspend fun analyze(wavPath: String, threshold: Float = 0.3f,
                        onProgress: (Int) -> Unit = {}
    ): List<AudioEvent> = withContext(Dispatchers.Default) { emptyList() }
    fun release() {}
}
