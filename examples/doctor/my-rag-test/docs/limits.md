# Limits and quotas

The Free plan includes 3 projects, 1 GB of storage and 10,000 API requests per month. The Team plan includes 50 projects, 100 GB of storage and 5 million API requests per month.

API rate limits are 20 requests per second on the Free plan and 500 requests per second on the Team plan. Requests above the limit receive HTTP status 429 with a Retry-After header.

File uploads are limited to 2 GB per file. Larger files must be uploaded with the multipart upload API in parts of at least 5 MB.
