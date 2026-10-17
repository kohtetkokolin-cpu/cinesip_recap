package com.cinesip.recap.ai

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.tensorflow.lite.Interpreter
import java.io.File
import java.io.FileInputStream
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import kotlin.math.exp

/** Audio event at a timestamp. */
data class AudioEvent(val start: Double, val label: String, val score: Float)

/**
 * On-device audio event detection via YAMNet (TFLite).
 * Port of backend/pipeline/audioevents.py. CPU-only, universal.
 * YAMNet: 16kHz mono, 0.96s windows, 521 classes.
 */
class AudioEvents(private val ctx: Context) {
    private var interp: Interpreter? = null

    // Subset of YAMNet labels we care about (indices into full 521 list)
    private val WANTED = mapOf(
        0 to "speech", 1 to "child_speech", 2 to "conversation",
        494 to "silence", 132 to "music", 137 to "movie_music",
        302 to "crying", 300 to "laughter", 306 to "shout",
        316 to "scream", 281 to "explosion", 290 to "gunshot"
    )

    fun isAvailable(): Boolean =
        AiManager.capabilities(ctx)["audio_events"] == true

    private fun init(): Boolean {
        if (interp != null) return true
        return try {
            val f = AiManager.modelFile(ctx, "yamnet")
            val buf: MappedByteBuffer =
                FileInputStream(f).channel.map(FileChannel.MapMode.READ_ONLY, 0, f.length())
            interp = Interpreter(buf, Interpreter.Options().apply {
                setNumThreads(2)  // CPU-only, conservative for all phones
            })
            true
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Run YAMNet over a 16kHz mono wav. Returns events per ~1s window.
     * Only emits the top wanted label per window above threshold.
     */
    suspend fun analyze(wavPath: String,
                        threshold: Float = 0.3f,
                        onProgress: (Int) -> Unit = {}
    ): List<AudioEvent> = withContext(Dispatchers.Default) {
        if (!init()) return@withContext emptyList()
        try {
            val samples = readWav16kMono(wavPath)
            val winSize = 15600  // 0.975s @16kHz (YAMNet expects 15600)
            val out = mutableListOf<AudioEvent>()
            var pos = 0
            var t = 0.0
            while (pos + winSize <= samples.size) {
                val window = Array(1) { samples.copyOfRange(pos, pos + winSize) }
                val scores = Array(1) { FloatArray(521) }
                interp!!.run(window, scores)
                // find best wanted label
                var bestLabel = ""; var bestScore = 0f
                for ((idx, label) in WANTED) {
                    val s = scores[0][idx]
                    if (s > bestScore) { bestScore = s; bestLabel = label }
                }
                if (bestScore >= threshold && bestLabel.isNotEmpty()) {
                    out.add(AudioEvent(t, bestLabel, bestScore))
                }
                pos += winSize
                t += 0.975
                onProgress((pos * 100 / samples.size.coerceAtLeast(1)))
            }
            out
        } catch (e: Exception) {
            emptyList()
        }
    }

    fun release() { interp?.close(); interp = null }

    private fun readWav16kMono(path: String): FloatArray {
        val bytes = File(path).readBytes()
        val dataStart = 44
        val n = (bytes.size - dataStart) / 2
        return FloatArray(n) { i ->
            val lo = bytes[dataStart + i * 2].toInt() and 0xFF
            val hi = bytes[dataStart + i * 2 + 1].toInt()
            ((hi shl 8) or lo).toShort() / 32768f
        }
    }
}
