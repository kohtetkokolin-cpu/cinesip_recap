package com.cinesip.recap.ai

import android.content.Context
import android.graphics.Bitmap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.opencv.android.OpenCVLoader
import org.opencv.core.Mat
import org.opencv.core.MatOfFloat
import org.opencv.imgcodecs.Imgcodecs
import org.opencv.objdetect.FaceDetectorYN
import org.opencv.objdetect.FaceRecognizerSF
import kotlin.math.sqrt

/** A detected face with embedding. */
data class Face(val box: FloatArray, val embedding: FloatArray)

/**
 * On-device face detection (YuNet) + recognition (SFace) + clustering.
 * Port of backend/pipeline/faceid.py. CPU-only, universal.
 */
class FaceKit(private val ctx: Context) {
    private var detector: FaceDetectorYN? = null
    private var recognizer: FaceRecognizerSF? = null

    fun isAvailable(): Boolean =
        AiManager.capabilities(ctx)["faces"] == true

    private fun init(): Boolean {
        if (detector != null) return true
        return try {
            if (!OpenCVLoader.initDebug()) return false
            val yunet = AiManager.modelFile(ctx, "yunet").absolutePath
            val sface = AiManager.modelFile(ctx, "sface").absolutePath
            detector = FaceDetectorYN.create(yunet, "", org.opencv.core.Size(320.0, 320.0), 0.7f, 0.3f, 5000)
            recognizer = FaceRecognizerSF.create(sface, "")
            true
        } catch (e: Exception) {
            false
        }
    }

    /** Detect + embed faces in a frame image file. Returns faces. */
    suspend fun processFrame(framePath: String): List<Face> =
        withContext(Dispatchers.Default) {
            if (!init()) return@withContext emptyList()
            try {
                val img: Mat = Imgcodecs.imread(framePath)
                if (img.empty()) return@withContext emptyList()
                detector!!.setInputSize(img.size())
                val faces = Mat()
                detector!!.detect(img, faces)
                val out = mutableListOf<Face>()
                for (r in 0 until faces.rows()) {
                    val row = FloatArray(15) { c -> faces.get(r, c)[0].toFloat() }
                    val aligned = Mat()
                    // alignCrop needs the row WITHOUT trailing score
                    val faceRow = Mat(1, 14, faces.type())
                    for (c in 0 until 14) faceRow.put(0, c, row[c].toDouble())
                    recognizer!!.alignCrop(img, faceRow, aligned)
                    val feat = MatOfFloat()
                    recognizer!!.feature(aligned, feat)
                    val emb = FloatArray(feat.rows()) { i -> feat.get(i, 0)[0].toFloat() }
                    out.add(Face(box = row.sliceArray(0..3), embedding = emb))
                    aligned.release(); faceRow.release()
                }
                img.release(); faces.release()
                out
            } catch (e: Exception) {
                emptyList()
            }
        }

    companion object {
        /** Cosine similarity 0..1. */
        fun similarity(a: FloatArray, b: FloatArray): Double {
            var dot = 0.0; var na = 0.0; var nb = 0.0
            for (i in a.indices) {
                dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]
            }
            if (na == 0.0 || nb == 0.0) return 0.0
            return dot / (sqrt(na) * sqrt(nb))
        }

        /**
         * Greedy clustering by cosine similarity (threshold 0.5).
         * Returns cluster id per face index. Port of faceid.py.
         */
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
