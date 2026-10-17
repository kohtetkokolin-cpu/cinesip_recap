package com.cinesip.recap.ai

import android.content.Context
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.nio.FloatBuffer
import kotlin.math.*

/** A transcribed segment with timestamps. */
data class Segment(val start: Double, val end: Double, val text: String)

/**
 * On-device transcription via ONNX Runtime + Whisper ONNX model.
 * CPU-only, universal (all phones). Models downloaded on first run.
 *
 * Uses sherpa-onnx Whisper tiny multilingual (int8):
 *  - whisper-encoder.int8.onnx
 *  - whisper-decoder.int8.onnx
 *  - whisper-tokens.txt
 */
class Transcriber(private val ctx: Context) {
    private var env: OrtEnvironment? = null
    private var encoder: OrtSession? = null
    private var decoder: OrtSession? = null
    private var tokens: List<String> = emptyList()

    // Whisper special tokens (multilingual tiny)
    private val SOT = 50258
    private val EOT = 50257
    private val NO_TIMESTAMPS = 50363

    fun isAvailable(): Boolean {
        val d = AiManager.modelDir(ctx)
        return File(d, "whisper-encoder.int8.onnx").exists() &&
               File(d, "whisper-decoder.int8.onnx").exists() &&
               File(d, "whisper-tokens.txt").exists()
    }

    private fun init(): Boolean {
        if (encoder != null) return true
        return try {
            val d = AiManager.modelDir(ctx)
            env = OrtEnvironment.getEnvironment()
            val opts = OrtSession.SessionOptions().apply { setNumThreads(2) }
            encoder = env!!.createSession(
                File(d, "whisper-encoder.int8.onnx").absolutePath, opts)
            decoder = env!!.createSession(
                File(d, "whisper-decoder.int8.onnx").absolutePath, opts)
            tokens = File(d, "whisper-tokens.txt").readLines()
            true
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Transcribe 16kHz mono wav. 30s chunks to bound memory.
     */
    suspend fun transcribe(wavPath: String,
                           onProgress: (Int) -> Unit = {}
    ): List<Segment> = withContext(Dispatchers.Default) {
        if (!init()) return@withContext emptyList()
        val samples = readWav16kMono(wavPath)
        val chunkSize = 16000 * 30
        val out = mutableListOf<Segment>()
        var processed = 0
        while (processed < samples.size) {
            val end = minOf(processed + chunkSize, samples.size)
            val chunk = samples.copyOfRange(processed, end)
            val text = transcribeChunk(chunk).trim()
            if (text.isNotEmpty()) {
                out.add(Segment(processed / 16000.0, end / 16000.0, text))
            }
            processed = end
            onProgress(processed * 100 / samples.size.coerceAtLeast(1))
        }
        out
    }

    private fun transcribeChunk(samples: FloatArray): String {
        // 1. Mel spectrogram (80 bins, 30s → 3000 frames)
        val mel = logMelSpectrogram(samples)  // [80, 3000]
        val nFrames = mel[0].size
        // 2. Encoder
        val encInput = OnnxTensor.createTensor(env,
            FloatBuffer.wrap(mel.flatMap { it.asList() }.toFloatArray()),
            longArrayOf(1, 80, nFrames.toLong()))
        val encOut = encoder!!.run(mapOf("mel" to encInput))
        @Suppress("UNCHECKED_CAST")
        val memory = (encOut[0].value as Array<Array<FloatArray>>)[0] // [seq, dim]
        encInput.close(); encOut.close()

        // 3. Autoregressive decode (greedy)
        val seqLen = memory.size
        val dim = memory[0].size
        val generated = mutableListOf(SOT, NO_TIMESTAMPS)
        val maxTokens = 100
        repeat(maxTokens) {
            val tLen = generated.size
            val tokInput = OnnxTensor.createTensor(env,
                FloatBuffer.wrap(FloatArray(tLen) { generated[it].toFloat() }),
                longArrayOf(1, tLen.toLong()))
            val memInput = OnnxTensor.createTensor(env,
                FloatBuffer.wrap(memory.flatMap { it.asList() }.toFloatArray()),
                longArrayOf(1, seqLen.toLong(), dim.toLong()))
            val decOut = decoder!!.run(mapOf(
                "tokens" to tokInput, "memory" to memInput))
            @Suppress("UNCHECKED_CAST")
            val logits = (decOut[0].value as Array<Array<FloatArray>>)[0]
            val last = logits[tLen - 1]
            var best = 0; var bestV = Float.NEGATIVE_INFINITY
            for (i in last.indices) {
                if (last[i] > bestV) { bestV = last[i]; best = i }
            }
            tokInput.close(); memInput.close(); decOut.close()
            if (best == EOT) return@repeat
            generated.add(best)
            if (generated.size > maxTokens + 2) return@repeat
        }
        // 4. Decode tokens to text (skip specials)
        return generated.filter { it < SOT && it >= 0 }
            .mapNotNull { tokens.getOrNull(it) }
            .joinToString("")
            .replace("▁", " ").trim()
    }

    fun release() {
        encoder?.close(); decoder?.close(); env?.close()
        encoder = null; decoder = null; env = null
    }

    /** 80-bin log-mel spectrogram. Simplified STFT. */
    private fun logMelSpectrogram(samples: FloatArray): Array<FloatArray> {
        val nFft = 400; val hop = 160; val nMels = 80
        val nFrames = maxOf(1, (samples.size - nFft) / hop + 1)
        val targetFrames = 3000
        val mel = Array(nMels) { FloatArray(targetFrames) }
        // Hann window
        val win = FloatArray(nFft) { i ->
            (0.5 * (1 - cos(2 * PI * i / nFft))).toFloat() }
        // mel filterbank (approx)
        val fb = melFilterbank(nFft / 2 + 1, nMels)
        val frames = minOf(nFrames, targetFrames)
        for (f in 0 until frames) {
            val start = f * hop
            // power spectrum via naive DFT (slow but simple; 30s chunk OK on CPU)
            val mag = FloatArray(nFft / 2 + 1)
            for (k in 0..nFft / 2) {
                var re = 0.0; var im = 0.0
                for (n in 0 until nFft) {
                    val s = if (start + n < samples.size)
                        samples[start + n] * win[n] else 0f
                    val a = 2 * PI * k * n / nFft
                    re += s * cos(a); im -= s * sin(a)
                }
                mag[k] = (re * re + im * im).toFloat()
            }
            for (m in 0 until nMels) {
                var v = 0f
                for (k in mag.indices) v += mag[k] * fb[m][k]
                mel[m][f] = (ln(maxOf(v, 1e-10).toDouble()) + 4.0)
                    .toFloat().coerceIn(0f, 8f) / 4f
            }
        }
        return mel
    }

    private fun melFilterbank(nBins: Int, nMels: Int): Array<FloatArray> {
        fun hz2mel(h: Double) = 2595 * log10(1 + h / 700)
        fun mel2hz(m: Double) = 700 * (10.0.pow(m / 2595) - 1)
        val sr = 16000.0
        val lo = hz2mel(0.0); val hi = hz2mel(sr / 2)
        val pts = DoubleArray(nMels + 2) { lo + (hi - lo) * it / (nMels + 1) }
            .map { mel2hz(it) / sr * nBins }
        return Array(nMels) { m ->
            FloatArray(nBins) { k ->
                val f = k.toDouble()
                when {
                    f < pts[m] || f > pts[m + 2] -> 0f
                    f <= pts[m + 1] ->
                        ((f - pts[m]) / (pts[m + 1] - pts[m])).toFloat()
                    else ->
                        ((pts[m + 2] - f) / (pts[m + 2] - pts[m + 1])).toFloat()
                }
            }
        }
    }

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
