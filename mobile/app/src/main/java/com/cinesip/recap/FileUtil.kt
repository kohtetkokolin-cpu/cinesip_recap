package com.cinesip.recap

import android.content.Context
import android.database.Cursor
import android.net.Uri
import android.provider.MediaStore

object FileUtil {
    fun getPath(ctx: Context, uri: Uri): String? {
        if (uri.scheme == "file") return uri.path
        var cur: Cursor? = null
        return try {
            cur = ctx.contentResolver.query(uri,
                arrayOf(MediaStore.Video.Media.DATA), null, null, null)
            if (cur?.moveToFirst() == true)
                cur.getString(cur.getColumnIndexOrThrow(MediaStore.Video.Media.DATA))
            else null
        } catch (_: Exception) { null } finally { cur?.close() }
    }
}
