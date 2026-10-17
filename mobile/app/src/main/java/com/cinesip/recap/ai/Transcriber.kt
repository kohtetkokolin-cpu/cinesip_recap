package com.cinesip.recap.ai

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** A transcribed segment with timestamps. */
data class Segment(val start: Double, val end: Double, val text: String)

/**
 * On-device transcription — STUB for Phase 1.
 * Phase 2: ONNX Runtime + Whisper (build stabilized first).
 */
class Transcriber(private val ctx: Context) {
    fun isAvailable(): Boolean = false
    suspend fun transcribe(wavPath: String, onProgress: (Int) -> Unit = {}
    ): List<Segment> = withContext(Dispatchers.Default) { emptyList() }
    fun release() {}
}
