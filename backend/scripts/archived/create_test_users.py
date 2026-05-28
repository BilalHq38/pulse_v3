"""Create dummy test users for all 4 channels: WhatsApp, Facebook, Instagram, Email."""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")

COMPANY = "15f9e973-d9b4-4d1e-9c6d-22173e2b427a"


def uid():
    return str(uuid.uuid4())


CUSTOMER_INSERT = """
    INSERT INTO customers(id, company_id, name, phone, email, segment, avatar,
        lifecycle_stage, lifetime_value, avg_sentiment, recent_tickets,
        complaint_count, days_since_last_contact, total_conversations,
        created_at, updated_at)
    VALUES($1,$2,$3,$4,$5,'general','','customer',0,0,0,0,1,1,NOW(),NOW())
    ON CONFLICT DO NOTHING
"""

CONVO_INSERT = """
    INSERT INTO conversations(id, company_id, customer_id, customer_name,
        channel, channel_id, subject, status, priority, ai_handled,
        sentiment_score, sentiment_label, message_count, last_message,
        last_message_at, unread_count, created_at, updated_at)
    VALUES($1,$2,$3,$4,$5,$6,$7,'open','medium',TRUE,0,'neutral',$8,$9,NOW(),$8,NOW(),NOW())
    ON CONFLICT DO NOTHING
"""

MSG_INSERT = """
    INSERT INTO messages(id, company_id, conversation_id, content,
        sender_type, sender_id, sender_name, read, created_at)
    VALUES($1,$2,$3,$4,'customer',$5,$6,FALSE,$7)
    ON CONFLICT DO NOTHING
"""


async def run():
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    # ── WhatsApp ──────────────────────────────────────────────────────────────
    wa_cid, wa_conv = uid(), uid()
    await conn.execute(CUSTOMER_INSERT, wa_cid, COMPANY,
                       "Ali Hassan", "+923001234567", "ali.hassan@example.com")
    await conn.execute(
        "INSERT INTO customer_channels(customer_id,channel,is_active,is_visible,created_at,updated_at) "
        "VALUES($1,'whatsapp',TRUE,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",
        wa_cid,
    )
    msgs_wa = [
        ("Hello! I heard about your jewelry collection.", "5 minutes"),
        ("What rings do you have available?", "3 minutes"),
        ("Can you show me photos of the Aurora Cuff?", "1 minute"),
    ]
    await conn.execute(CONVO_INSERT, wa_conv, COMPANY, wa_cid, "Ali Hassan",
                       "whatsapp", "+923001234567", "WhatsApp — Ali Hassan",
                       len(msgs_wa), msgs_wa[-1][0])
    import datetime
    for content, minutes in msgs_wa:
        ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=int(minutes.split()[0]))
        await conn.execute(MSG_INSERT, uid(), COMPANY, wa_conv,
                           content, wa_cid, "Ali Hassan", ago)
    print(f"WhatsApp  : customer={wa_cid[:8]}  conversation={wa_conv[:8]}")

    # ── Facebook ─────────────────────────────────────────────────────────────
    fb_cid, fb_conv = uid(), uid()
    await conn.execute(CUSTOMER_INSERT, fb_cid, COMPANY,
                       "Sara Khan", "", "sara.khan@example.com")
    await conn.execute(
        "INSERT INTO customer_channels(customer_id,channel,is_active,is_visible,created_at,updated_at) "
        "VALUES($1,'facebook',TRUE,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",
        fb_cid,
    )
    await conn.execute(
        "INSERT INTO customer_social_profiles(customer_id,platform,profile_id,is_active,is_visible,created_at,updated_at) "
        "VALUES($1,'facebook','fb_sara_khan_001',TRUE,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",
        fb_cid,
    )
    msgs_fb = [
        ("Hi! I found you through Facebook.", "12 minutes"),
        ("Do you have necklaces? Can you send photos?", "5 minutes"),
        ("What is the price of the Aurelian Sun necklace?", "2 minutes"),
    ]
    await conn.execute(CONVO_INSERT, fb_conv, COMPANY, fb_cid, "Sara Khan",
                       "facebook", "fb_sara_khan_001", "Facebook — Sara Khan",
                       len(msgs_fb), msgs_fb[-1][0])
    for content, minutes in msgs_fb:
        ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=int(minutes.split()[0]))
        await conn.execute(MSG_INSERT, uid(), COMPANY, fb_conv,
                           content, fb_cid, "Sara Khan", ago)
    print(f"Facebook  : customer={fb_cid[:8]}  conversation={fb_conv[:8]}")

    # ── Instagram ────────────────────────────────────────────────────────────
    ig_cid, ig_conv = uid(), uid()
    await conn.execute(CUSTOMER_INSERT, ig_cid, COMPANY,
                       "Zain Ahmed", "", "zain.ahmed@example.com")
    await conn.execute(
        "INSERT INTO customer_channels(customer_id,channel,is_active,is_visible,created_at,updated_at) "
        "VALUES($1,'instagram',TRUE,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",
        ig_cid,
    )
    await conn.execute(
        "INSERT INTO customer_social_profiles(customer_id,platform,profile_id,is_active,is_visible,created_at,updated_at) "
        "VALUES($1,'instagram','ig_zain_ahmed_002',TRUE,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",
        ig_cid,
    )
    msgs_ig = [
        ("Loved your post! What earrings do you sell?", "20 minutes"),
        ("Show me images of the Lunar Eclipse earrings please", "8 minutes"),
        ("I want to buy the Lunar Eclipse. How much is it?", "3 minutes"),
    ]
    await conn.execute(CONVO_INSERT, ig_conv, COMPANY, ig_cid, "Zain Ahmed",
                       "instagram", "ig_zain_ahmed_002", "Instagram — Zain Ahmed",
                       len(msgs_ig), msgs_ig[-1][0])
    for content, minutes in msgs_ig:
        ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=int(minutes.split()[0]))
        await conn.execute(MSG_INSERT, uid(), COMPANY, ig_conv,
                           content, ig_cid, "Zain Ahmed", ago)
    print(f"Instagram : customer={ig_cid[:8]}  conversation={ig_conv[:8]}")

    # ── Email ────────────────────────────────────────────────────────────────
    em_cid, em_conv = uid(), uid()
    await conn.execute(CUSTOMER_INSERT, em_cid, COMPANY,
                       "Maria Malik", "", "maria.malik@example.com")
    await conn.execute(
        "INSERT INTO customer_channels(customer_id,channel,is_active,is_visible,created_at,updated_at) "
        "VALUES($1,'email',TRUE,TRUE,NOW(),NOW()) ON CONFLICT DO NOTHING",
        em_cid,
    )
    msgs_em = [
        ("Hello, I am interested in your bracelet collection as a gift.", "30 minutes"),
        ("Do you have any bracelets under 1000 USD?", "15 minutes"),
        ("Please send me photos of the Stellar Drift bracelet.", "4 minutes"),
    ]
    await conn.execute(CONVO_INSERT, em_conv, COMPANY, em_cid, "Maria Malik",
                       "email", "maria.malik@example.com", "Enquiry — Bracelets",
                       len(msgs_em), msgs_em[-1][0])
    for content, minutes in msgs_em:
        ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=int(minutes.split()[0]))
        await conn.execute(MSG_INSERT, uid(), COMPANY, em_conv,
                           content, em_cid, "Maria Malik", ago)
    print(f"Email     : customer={em_cid[:8]}  conversation={em_conv[:8]}")

    # ── Verify ────────────────────────────────────────────────────────────────
    rows = await conn.fetch(
        "SELECT channel, COUNT(*) AS c FROM conversations "
        "WHERE company_id=$1 GROUP BY channel ORDER BY channel",
        COMPANY,
    )
    print()
    print("=== Conversations by channel ===")
    for r in rows:
        print(f"  {r['channel']:<12}: {r['c']}")
    custs = await conn.fetchval(
        "SELECT COUNT(*) FROM customers WHERE company_id=$1", COMPANY
    )
    print(f"  Total customers : {custs}")

    await conn.close()


asyncio.run(run())
