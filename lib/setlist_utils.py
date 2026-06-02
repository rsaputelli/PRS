# lib/setlist_utils.py
"""Set list file upload and management utilities."""

from typing import Optional
from datetime import datetime
from supabase import Client


def upload_setlist_to_storage(
    gig_id: str,
    file_bytes: bytes,
    file_ext: str,
    sb_client: Client,
) -> Optional[str]:
    """
    Upload set list file to Supabase storage and return public URL.
    
    Args:
        gig_id: Gig UUID (used as folder in storage)
        file_bytes: File content
        file_ext: File extension (pdf, xlsx, xls)
        sb_client: Supabase client with service role key
    
    Returns:
        Public URL to the uploaded file, or None if upload failed
    """
    try:
        path = f"{gig_id}/setlist.{file_ext}"
        # Upload with upsert=true to replace old version
        sb_client.storage.from_("setlists").upload(
            path,
            file_bytes,
            {
                "content-type": _get_content_type(file_ext),
                "upsert": "true",
            },
        )
        # Get public URL
        public_response = sb_client.storage.from_("setlists").get_public_url(path)
        if isinstance(public_response, dict):
            return public_response.get("publicUrl")
        return public_response
    except Exception as e:
        raise Exception(f"Set list upload failed: {e}")


def delete_setlist_from_storage(gig_id: str, sb_client: Client) -> bool:
    """
    Delete set list file from Supabase storage.
    
    Args:
        gig_id: Gig UUID
        sb_client: Supabase client with service role key
    
    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        # Try to delete both PDF and Excel versions (in case of format change)
        for ext in ["pdf", "xlsx", "xls"]:
            try:
                path = f"{gig_id}/setlist.{ext}"
                sb_client.storage.from_("setlists").remove([path])
            except Exception:
                # File may not exist; continue
                pass
        return True
    except Exception as e:
        raise Exception(f"Set list deletion failed: {e}")


def _get_content_type(file_ext: str) -> str:
    """Map file extension to content type."""
    ext_lower = (file_ext or "").lower().strip(".")
    mapping = {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
    }
    return mapping.get(ext_lower, "application/octet-stream")
