package com.cinesip.recap.ai

import android.content.Context
import java.io.File

/**
 * AI model manager — STUB for Phase 1.
 * Phase 2: downloads Whisper/OpenCV/YAMNet models on first run.
 */
object AiManager {
    fun modelDir(ctx: Context): File =
        File(ctx.getExternalFilesDir(null), "models").apply { mkdirs() }

    fun capabilities(ctx: Context): Map<String, Boolean> = mapOf(
        "transcribe" to false,  // Phase 2
        "faces" to false,       // Phase 2
        "audio_events" to false // Phase 2
    )

    suspend fun ensureModels(ctx: Context, onProgress: (String, Int) -> Unit
    ): Pair<List<String>, List<String>> {
        onProgress("phase2", 100)
        return Pair(emptyList(), listOf("transcribe", "faces", "audio_events"))
    }
}
