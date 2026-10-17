package com.cinesip.recap.ai

import android.content.Context
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineWhisperModelConfig
import com.k2fsa.sherpa.onnx.FeatureExtractorConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/** A transcribed segment with timestamps. */
data class Segment(val start: Double, val end: Double, val text: String)

/**
 * On-device transcription via sherpa-onnx (Whisper).
 * CPU-only, universal. Falls back gracefully if model missing.
 */
class Transcriber(private val ctx: Context) {
    private var rec: OfflineRecognizer? = null

    fun isAvailable(): Boolean =
        AiManager.capabilities(ctx)["transcribe"] == true

    private fun init(): Boolean {
        if (rec != null) return true
        return try {
            val dir = AiManager.modelDir(ctx)
            val enc = File(dir, "whisper-encoder.onnx").absolutePath
            val dec = File(dir, "whisper-decoder.onnx").absolutePath
            val tokens = File(dir, "whisper-tokens.txt").absolutePath
            val cfg = OfflineRecognizerConfig(
                featConfig = FeatureExtractorConfig(sampleRate = 16000, featureDim = 80),
                modelConfig = OfflineModelConfig(
                    whisper = OfflineWhisperModelConfig(
                        encoder = enc, decoder = dec,
                        language = "", task = "transcribe"
                    ),
                    tokens = tokens,
                    numThreads = 2, debug = false, provider = "cpu",
                    modelType = "whisper"
                ),
                decodingMethod = "greedy_search",
                maxActivePaths = 4
            )
            rec = OfflineRecognizer(cfg)
            true
        } catch (e: Exception) {
            rec = null
            false
        }
    }

    /**
     * Transcribe a 16kHz mono wav file. Returns segments.
     * Processes in 30s chunks to bound memory on low-end phones.
     */
    suspend fun transcribe(wavPath: String,
                           onProgress: (Int) -> Unit = {}
    ): List<Segment> = withContext(Dispatchers.Default) {
        if (!init()) return@withContext emptyList()
        val samples = readWav16kMono(wavPath)
        val chunkSize = 16000 * 30  // 30s chunks
        val out = mutableListOf<Segment>()
        var processed = 0
        while (processed < samples.size) {
            val end = minOf(processed + chunkSize, samples.size)
            val chunk = samples.copyOfRange(processed, end)
            val stream = rec!!.createStream()
            stream.acceptWaveform(chunk, 16000)
            rec!!.decode(stream)
            val text = stream.result.text.trim()
            if (text.isNotEmpty()) {
                out.add(Segment(
                    start = processed / 16000.0,
                    end = end / 16000.0,
                    text = text
                ))
            }
            stream.release()
            processed = end
            onProgress((processed * 100 / samples.size.coerceAtLeast(1)))
        }
        out
    }

    fun release() { rec?.release(); rec = null }

    /** Minimal WAV reader: 16kHz mono 16-bit PCM. */
    private fun readWav16kMono(path: String): FloatArray {
        val bytes = File(path).readBytes()
        // skip 44-byte header (assumes PCM wav from our ffmpeg extract)
        val dataStart = 44
        val n = (bytes.size - dataStart) / 2
        return FloatArray(n) { i ->
            val lo = bytes[dataStart + i * 2].toInt() and 0xFF
            val hi = bytes[dataStart + i * 2 + 1].toInt()
            ((hi shl 8) or lo).toShort() / 32768f
        }
    }
}
