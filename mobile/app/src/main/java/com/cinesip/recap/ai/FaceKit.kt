package com.cinesip.recap.ai

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.sqrt

/** A detected face with embedding. */
data class Face(val box: FloatArray, val embedding: FloatArray)

/**
 * On-device face detection — STUB for Phase 1.
 * Phase 2: OpenCV YuNet + SFace (build stabilized first).
 */
class FaceKit(private val ctx: Context) {
    fun isAvailable(): Boolean = false
    suspend fun processFrame(framePath: String): List<Face> =
        withContext(Dispatchers.Default) { emptyList() }

    companion object {
        fun similarity(a: FloatArray, b: FloatArray): Double {
            var dot = 0.0; var na = 0.0; var nb = 0.0
            for (i in a.indices) {
                dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]
            }
            if (na == 0.0 || nb == 0.0) return 0.0
            return dot / (sqrt(na) * sqrt(nb))
        }
        fun cluster(embeddings: List<FloatArray>, threshold: Double = 0.5): IntArray {
            val n = embeddings.size
            val assign = IntArray(n) { -1 }
            var nextId = 0
            for (i in 0 until n) {
                if (assign[i] != -1) continue
                assign[i] = nextId
                for (j in i + 1 until n) {
                    if (assign[j] == -1 &&
                        similarity(embeddings[i], embeddings[j]) >= threshold) {
                        assign[j] = nextId
                    }
                }
                nextId++
            }
            return assign
        }
    }
}
