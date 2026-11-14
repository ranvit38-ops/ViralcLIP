export type TtConfig = { configured: boolean; missing: string[]; audited: boolean; redirect_uri: string };

export type TtRule = { enabled: boolean; time_of_day: string; timezone: string; clips_per_day: number; mode: TtMode; privacy_level: string | null };

export type TtAccount = {
  id: number; open_id: string; display_name: string; avatar_url: string | null; status: "ok" | "reauth_required";
  posts_today: number; daily_limit: number; rule: TtRule | null;
};

export type TtCreatorInfo = {
  creator_nickname: string; privacy_level_options: string[];
  comment_disabled: boolean; duet_disabled: boolean; stitch_disabled: boolean; max_video_post_duration_sec: number;
};

export type TtMode = "direct" | "draft";

export type TtPost = {
  id: number; clip_id: number; clip_title: string; clip_thumbnail_url: string | null; account_id: number; account_name: string;
  mode: TtMode; privacy_level: string | null; status: string; publish_at: string | null; timezone: string | null;
  publish_id: string | null; error: string | null; created_at: string; posted_at: string | null;
  platform: "tiktok"; // added client-side so queue/calendar can mix posts with YouTube uploads
};

export type TtStats = { scheduled: number; posting: number; published: number; drafts_sent: number; failed: number };

const PRIVACY: Record<string, string> = {
  PUBLIC_TO_EVERYONE: "Everyone", MUTUAL_FOLLOW_FRIENDS: "Friends", FOLLOWER_OF_CREATOR: "Followers", SELF_ONLY: "Only me",
};
export const privacyLabel = (p: string | null | undefined) => (p ? PRIVACY[p] ?? p.replaceAll("_", " ").toLowerCase() : "—");

/** Used only when creator_info can't be fetched (e.g. in the rule editor); TikTok's own list wins otherwise. */
export const FALLBACK_PRIVACY = Object.keys(PRIVACY);
