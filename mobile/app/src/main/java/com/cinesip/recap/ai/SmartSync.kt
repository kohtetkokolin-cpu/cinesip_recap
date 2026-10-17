package com.cinesip.recap.ai

/**
 * Port of backend/pipeline/smartsync.py — content-aware beat→scene matching.
 * Burmese keyword → category map, auto-naming via dialogue co-occurrence,
 * retrieval scoring with chronological anchor.
 */

// Beat data from Gemini script
data class Beat(
    val id: Int,
    val textMy: String,       // Burmese narration
    val visualQueryEn: String, // English visual query (from Gemini)
    val durTarget: Double      // target seconds
)

// A candidate scene window from the movie
data class SceneWindow(
    val start: Double,
    val end: Double,
    val faces: List<Int>,       // cluster ids present
    val audioLabels: List<String>,
    val motion: Double,         // 0..1
    val brightness: Double      // 0..1
)

data class Match(val beatId: Int, val scene: SceneWindow,
                 val score: Double, val reason: String)

object SmartSync {
    // Burmese keyword → category (longest-match-first, same as smartsync.py)
    private val KEYWORDS: List<Pair<String, String>> = listOf(
        // action
        "တိုက်ပွဲ" to "action", "တိုက်ခိုက်" to "action", "ပြေး" to "action",
        "လိုက်ဖမ်း" to "action", "ပစ်ခတ်" to "action", "ပေါက်ကွဲ" to "action",
        "ဓား" to "action", "သတ်" to "action",
        // sad
        "ငို" to "sad", "ဝမ်းနည်း" to "sad", "မျက်ရည်" to "sad",
        "သေဆုံး" to "sad", "လွမ်း" to "sad", "နာကျင်" to "sad",
        // happy
        "ရယ်" to "happy", "ပျော်" to "happy", "မင်္ဂလာ" to "happy",
        "အောင်" to "happy",  // NOTE: usually part of names — low weight
        // tense
        "ကြောက်" to "tense", "စိုးရိမ်" to "tense", "အန္တရာယ်" to "tense",
        "လျှို့ဝှက်" to "tense", "သံသယ" to "tense",
        // dialogue
        "ပြော" to "dialogue", "မေး" to "dialogue", "ဖြေ" to "dialogue",
        "ငြင်း" to "dialogue", "တိုင်" to "dialogue",
        // music
        "သီချင်း" to "music", "ဂီတ" to "music", "က" to "music"
    ).sortedByDescending { it.first.length }

    fun classifyBeat(text: String): String {
        var remaining = text
        val votes = mutableMapOf<String, Int>()
        for ((kw, cat) in KEYWORDS) {
            if (remaining.contains(kw)) {
                votes[cat] = (votes[cat] ?: 0) + 1
                remaining = remaining.replace(kw, "")
            }
        }
        return votes.maxByOrNull { it.value }?.key ?: "general"
    }

    /**
     * Score a scene for a beat. Higher = better match.
     * Chronological anchor: prefer scenes near the beat's expected position.
     */
    fun score(beat: Beat, scene: SceneWindow,
              beatExpectedStart: Double, movieDuration: Double,
              nameToCluster: Map<String, Int> = emptyMap()
    ): Pair<Double, String> {
        var s = 0.0
        val reasons = mutableListOf<String>()
        val cat = classifyBeat(beat.textMy)

        // 1. Category ↔ audio/visual alignment
        val audioSet = scene.audioLabels.toSet()
        when (cat) {
            "action" -> {
                if (audioSet.any { it in setOf("explosion", "gunshot", "shout") }) {
                    s += 2.0; reasons += "action-audio"
                }
                if (scene.motion > 0.6) { s += 1.5; reasons += "high-motion" }
            }
            "sad" -> {
                if (audioSet.any { it in setOf("crying", "silence") }) {
                    s += 2.0; reasons += "sad-audio"
                }
                if (scene.brightness < 0.4) { s += 1.0; reasons += "dark-mood" }
            }
            "happy" -> {
                if (audioSet.any { it in setOf("laughter", "music") }) {
                    s += 2.0; reasons += "happy-audio"
                }
                if (scene.brightness > 0.6) { s += 1.0; reasons += "bright-mood" }
            }
            "tense" -> {
                if (audioSet.contains("silence")) { s += 1.5; reasons += "tense-silence" }
                if (scene.motion < 0.3) { s += 1.0; reasons += "still-tension" }
            }
            "dialogue" -> {
                if (audioSet.any { it in setOf("speech", "conversation") }) {
                    s += 2.0; reasons += "has-dialogue"
                }
                if (scene.faces.isNotEmpty()) { s += 1.5; reasons += "faces-visible" }
            }
            "music" -> {
                if (audioSet.any { it in setOf("music", "movie_music") }) {
                    s += 2.0; reasons += "has-music"
                }
            }
        }

        // 2. Character presence: if beat mentions a name mapped to a cluster
        for ((name, cluster) in nameToCluster) {
            if (beat.textMy.contains(name) && scene.faces.contains(cluster)) {
                s += 3.0; reasons += "character:$name"
            }
        }

        // 3. Chronological anchor (prevents anachronistic matching)
        val sceneMid = (scene.start + scene.end) / 2
        val expectedRatio = (beatExpectedStart / movieDuration.coerceAtLeast(1.0))
            .coerceIn(0.0, 1.0)
        val sceneRatio = (sceneMid / movieDuration.coerceAtLeast(1.0))
            .coerceIn(0.0, 1.0)
        val drift = kotlin.math.abs(sceneRatio - expectedRatio)
        if (drift < 0.15) { s += 1.5; reasons += "in-window" }
        else if (drift > 0.4) { s -= 1.0; reasons += "out-of-window" }

        // 4. Duration fitness: scene should cover beat target
        val sceneLen = scene.end - scene.start
        if (sceneLen >= beat.durTarget * 0.8) { s += 0.5 }

        return Pair(s, reasons.joinToString(","))
    }

    /**
     * Match all beats to scenes. Falls back to sequential if no good match.
     * Returns matches in beat order.
     */
    fun smartSelect(
        beats: List<Beat>,
        scenes: List<SceneWindow>,
        movieDuration: Double,
        nameToCluster: Map<String, Int> = emptyMap(),
        minScore: Double = 1.0
    ): List<Match> {
        if (scenes.isEmpty()) return emptyList()
        val used = mutableSetOf<Int>()
        // expected position: beats spread across movie
        return beats.mapIndexed { bi, beat ->
            val expectedStart = (bi.toDouble() / beats.size) * movieDuration
            var best: SceneWindow? = null
            var bestScore = Double.NEGATIVE_INFINITY
            var bestReason = ""
            scenes.forEachIndexed { si, scene ->
                if (si in used) return@forEachIndexed
                val (sc, reason) = score(beat, scene, expectedStart, movieDuration, nameToCluster)
                if (sc > bestScore) { bestScore = sc; best = scene; bestReason = reason }
            }
            if (best != null && bestScore >= minScore) {
                used.add(scenes.indexOf(best))
                Match(beat.id, best, bestScore, bestReason)
            } else {
                // Fallback: sequential (never worse than before)
                val si = (bi * scenes.size / beats.size.coerceAtLeast(1))
                    .coerceIn(0, scenes.size - 1)
                val fb = scenes[si]
                Match(beat.id, fb, 0.0, "fallback-sequential")
            }
        }
    }
}
