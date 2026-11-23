"""Demo knowledge base: the help centre of a fictional SaaS product, "Nimbus Cloud".

All names, prices and policies are invented for the demo.
"""

DOCS = [
    {"doc_id": "billing-refunds", "title": "Refund policy", "text": """Annual plans can be refunded in full within 30 days of purchase. After 30 days, annual plans are refunded pro rata for the unused months, minus a 10% processing fee.

Monthly plans are not refundable, but you can cancel at any time and keep access until the end of the current billing period.

Refunds are issued to the original payment method and usually arrive within 5 to 7 business days. Contact billing@nimbus.example to request a refund.""",
     "questions": ["What is the refund policy for annual plans?", "Can I get a refund on a monthly plan?",
                   "How long does a refund take to arrive?", "Is there a processing fee on annual refunds after 30 days?"]},
    {"doc_id": "pricing-plans", "title": "Plans and pricing", "text": """Nimbus Cloud has three plans. The Starter plan is free and includes 5 GB of storage and 2 team members.

The Pro plan costs $29 per user per month and includes 1 TB of storage per user, version history for 180 days and priority email support.

The Enterprise plan has custom pricing and adds single sign-on, audit logs, a 99.95% uptime SLA and a dedicated customer success manager.""",
     "questions": ["How much does the Pro plan cost per user?", "How much storage does the Starter plan include?",
                   "What does the Enterprise plan add?", "How long is version history kept on the Pro plan?"]},
    {"doc_id": "sso-setup", "title": "Single sign-on", "text": """Single sign-on is available on the Enterprise plan. Nimbus supports SAML 2.0 and OpenID Connect identity providers, and enforces SSO for every member once it is enabled.

To connect Okta, create a new app integration in the Okta admin console, paste the Nimbus ACS URL from Settings, and upload the signing certificate.

To connect Azure AD, register Nimbus as an enterprise application, set the reply URL, and assign the users or groups who should get access.""",
     "questions": ["How do I connect Okta for SAML single sign-on on Enterprise?",
                   "Which identity providers does Nimbus single sign-on support?",
                   "How do I connect Azure AD to Nimbus?"]},
    {"doc_id": "api-rate-limits", "title": "API rate limits", "text": """The REST API allows 600 requests per minute per API token on the Pro plan and 3000 requests per minute on Enterprise. Starter tokens are limited to 60 requests per minute.

When you exceed the limit the API returns HTTP 429 with a Retry-After header. Clients should back off exponentially and retry after the indicated number of seconds.

Bulk endpoints count as one request per call, so batching uploads is the best way to stay within the rate limit.""",
     "questions": ["What is the API rate limit on the Pro plan?", "What happens when I exceed the API rate limit?",
                   "How many API requests per minute are allowed on Enterprise?"]},
    {"doc_id": "data-retention", "title": "Data retention", "text": """Deleted files stay in the trash for 30 days and can be restored by any editor. After 30 days they are permanently purged and cannot be recovered.

Audit logs are retained for 1 year on the Enterprise plan. Activity history on Pro is retained for 90 days.

When an account is closed, all customer data is deleted from production systems within 30 days and from encrypted backups within 90 days.""",
     "questions": ["How long do deleted files stay in the trash?", "How long are audit logs retained?",
                   "When is data deleted after an account is closed?"]},
    {"doc_id": "backups", "title": "Backups and disaster recovery", "text": """Nimbus takes encrypted backups of all customer data every 6 hours and keeps them for 35 days.

Backups are stored in a second region, separate from the primary region. The recovery point objective is 6 hours and the recovery time objective is 4 hours.

Customers cannot restore individual backups themselves; use version history or the trash to recover individual files.""",
     "questions": ["How often does Nimbus back up customer data?", "What is the recovery time objective?",
                   "Can I restore a backup myself?"]},
    {"doc_id": "regions", "title": "Data residency and regions", "text": """Workspaces can be hosted in the US (Virginia), EU (Frankfurt) or APAC (Singapore) region. The region is chosen when the workspace is created.

Moving an existing workspace to another region is available on Enterprise and requires a migration window of up to 48 hours.

Data at rest never leaves the selected region, except for encrypted backups which are stored in a paired region on the same continent.""",
     "questions": ["Which regions can host a Nimbus workspace?", "Can I move my workspace to another region?",
                   "Does my data leave the selected region?"]},
    {"doc_id": "password-reset", "title": "Resetting your password", "text": """To reset your password, click Forgot password on the sign-in page and enter your work email. The reset link expires after 60 minutes.

If your workspace uses single sign-on, passwords are managed by your identity provider and the Forgot password link is disabled.

Admins can force a password reset for any member from the Members page in the admin console.""",
     "questions": ["How do I reset my password?", "How long is the password reset link valid?",
                   "Can an admin force a password reset for a member?"]},
    {"doc_id": "two-factor", "title": "Two-factor authentication", "text": """Two-factor authentication supports authenticator apps and hardware security keys. SMS codes are not supported.

Admins can require two-factor authentication for every member of the workspace from the Security settings page.

If you lose your device, use one of the ten recovery codes generated during setup, or ask a workspace admin to reset your second factor.""",
     "questions": ["Does two-factor authentication support SMS codes?", "How do I recover if I lose my two-factor device?",
                   "Can admins require two-factor authentication?"]},
    {"doc_id": "invoices", "title": "Invoices and payment methods", "text": """Invoices are emailed to the billing contact on the first day of each billing cycle and are also available under Settings, Billing.

Nimbus accepts Visa, Mastercard and American Express. Enterprise customers can also pay by bank transfer with net 30 payment terms.

To change the billing contact or add a VAT number, open Settings, Billing, and edit the company details.""",
     "questions": ["Where can I find my invoices?", "Which payment methods does Nimbus accept?",
                   "How do I add a VAT number to my invoices?"]},
    {"doc_id": "uptime-sla", "title": "Uptime SLA", "text": """The Enterprise plan includes a 99.95% monthly uptime SLA. Scheduled maintenance announced 72 hours in advance does not count as downtime.

If monthly uptime falls below 99.95%, Enterprise customers receive a 10% service credit. Below 99.0%, the credit rises to 25%.

Service credits must be requested within 30 days of the end of the affected month.""",
     "questions": ["What uptime does the Enterprise SLA guarantee?", "What service credit do I get if uptime falls below 99%?",
                   "How long do I have to request a service credit?"]},
    {"doc_id": "support-hours", "title": "Support channels and hours", "text": """Starter customers get community forum support. Pro customers get priority email support with a first response within 8 business hours.

Enterprise customers get 24/7 support by email and phone, with a first response within 1 hour for severity-one incidents.

Business hours are 9:00 to 18:00 in the customer's selected region, Monday to Friday.""",
     "questions": ["What is the support response time on the Pro plan?", "Is 24/7 support available?",
                   "What are Nimbus support business hours?"]},
    {"doc_id": "data-export", "title": "Exporting your data", "text": """Workspace owners can export all files and metadata from Settings, Data export. Exports are delivered as a ZIP archive.

Exports larger than 50 GB are split into multiple archives. A download link is emailed when the export is ready and stays valid for 7 days.

The API also exposes an export endpoint for automated backups to your own storage.""",
     "questions": ["How do I export all my workspace data?", "How long is the export download link valid?",
                   "What happens to exports larger than 50 GB?"]},
    {"doc_id": "team-roles", "title": "Roles and permissions", "text": """Nimbus has four roles: owner, admin, editor and viewer. Only owners can delete the workspace or change the plan.

Admins manage members, security settings and integrations. Editors can create, edit and share files. Viewers can only view and comment.

Guests from outside your organisation can be invited as viewers on individual folders without using a paid seat.""",
     "questions": ["What roles does Nimbus have?", "Who can delete the workspace?",
                   "Do guest viewers use a paid seat?"]},
    {"doc_id": "webhooks", "title": "Webhooks", "text": """Webhooks send an HTTP POST to your endpoint when files are created, updated, shared or deleted.

Each delivery is signed with an HMAC SHA-256 signature in the X-Nimbus-Signature header. Verify it with your webhook secret before trusting the payload.

Failed deliveries are retried up to 8 times over 24 hours with exponential backoff, after which the webhook is disabled.""",
     "questions": ["How are webhook deliveries signed?", "How many times are failed webhook deliveries retried?",
                   "Which events trigger a webhook?"]},
    {"doc_id": "storage-limits", "title": "Storage and upload limits", "text": """The maximum size of a single uploaded file is 250 GB using the desktop app and 5 GB using the web browser.

When a workspace reaches its storage quota, uploads are blocked but existing files remain accessible. Admins can buy extra storage in 1 TB blocks.

Version history counts towards the storage quota on the Starter plan only.""",
     "questions": ["What is the maximum file upload size in the browser?", "What happens when storage is full?",
                   "Can I buy extra storage?"]},
    {"doc_id": "gdpr", "title": "GDPR and privacy", "text": """Nimbus acts as a data processor for customer content. A data processing agreement is available to all paid plans from the Legal page.

The list of subprocessors is published on the Trust Center and customers are notified 30 days before a new subprocessor is added.

Data subject requests can be submitted to privacy@nimbus.example and are answered within 30 days.""",
     "questions": ["Is a data processing agreement available?", "How are customers notified about new subprocessors?",
                   "How quickly are data subject requests answered?"]},
    {"doc_id": "incident-process", "title": "Incident communication", "text": """Service incidents are posted on status.nimbus.example within 15 minutes of detection.

Enterprise customers receive email updates every 60 minutes until the incident is resolved.

A written post-incident review is published within 5 business days for any incident with more than 30 minutes of customer impact.""",
     "questions": ["Where are service incidents posted?", "When is a post-incident review published?",
                   "How often do Enterprise customers get incident updates?"]},
]

# Pricing change shipped on demo day 9 (the serving index keeps the old version for two days).
PRICING_V2 = """Nimbus Cloud has three plans. The Starter plan is free and includes 10 GB of storage and 3 team members.

The Pro plan costs $35 per user per month and includes 2 TB of storage per user, version history for 365 days and priority email support.

The Enterprise plan has custom pricing and adds single sign-on, audit logs, a 99.95% uptime SLA and a dedicated customer success manager."""

# Questions about features the help centre does not document yet (the demo's emerging topic).
GAP_QUESTIONS = [
    "Does the mobile app work offline on Android?",
    "Can the mobile app sync photos automatically from my phone camera?",
    "Is there an offline mode for the iPhone mobile app?",
    "How do I enable the AI assistant to summarise meeting recordings?",
    "Can the AI assistant translate meeting transcripts?",
    "Does the AI assistant use my recordings to train models?",
    "Is there a Linux desktop client?",
    "Does the mobile app support face unlock?",
]

# Questions whose answer is split across two adjacent chunks (chunking defect).
SPLIT_QUESTIONS = {
    "How do I connect Okta for SAML single sign-on on Enterprise?": "sso-setup",
}
