# payment-rail-reference

Dated, sourced, machine-readable parameters for US payment rails. Every value
carries the URL it came from and the date somebody checked it.

## Why this exists

Rail parameters change a few times a year, and when one changes, every page on
the web that quotes it is wrong from that morning. Nothing announces this. You
find out when an integration rejects a payment, or when a sponsor bank asks a
question you answer confidently and incorrectly.

The FedNow limit is the clearest example. It was $1,000,000 until 12 November
2025 and is $10,000,000 now. There is a large amount of published material,
including material dated 2026, still quoting the old figure or the original
launch ceilings. Ask an assistant and you will often get one of those numbers
back, because that is what most of the published material still says.

We got this wrong on our own site once. That is most of the reason this repo
exists.

## The data

One file per rail. Read them directly, they are meant to be legible.

| Rail | File | Current transaction limit |
|---|---|---|
| FedNow Service | [rails/fednow.yaml](rails/fednow.yaml) | $10,000,000 since 2025-11-12 |
| RTP Network | [rails/rtp.yaml](rails/rtp.yaml) | $10,000,000 since 2025-02-09 |
| ACH | [rails/ach.yaml](rails/ach.yaml) | Same Day $1,000,000, rising to $10,000,000 on 2027-09-17 |
| Fedwire Funds Service | [rails/fedwire.yaml](rails/fedwire.yaml) | Up to $10,000,000,000, stated to the nearest billion |

## How to read a fact

Every fact is a block, and every block carries its own provenance:

```yaml
network_transaction_limit:
  value_usd: 10000000
  effective_from: 2025-02-09
  supersedes:
    value_usd: 1000000
    effective_from: 2022-04
  status: verified
  source: https://www.theclearinghouse.org/payment-systems/Articles/2024/12/Higher_10_Million_RTP_Network_Transaction_Limit_Empowers_New_Uses_12-04-2024
  verified_on: 2026-10-06
```

`status` is one of:

- `verified`, meaning a primary source states it directly
- `verified_partial`, meaning part of it is stated and part is not

`supersedes` matters more than it looks. If you are debugging a payment that was
initiated last year, the limit that applied then is the one you need, not the
one in force today.

## What is deliberately missing

Each file has a `gaps` section. Those are fields we could not confirm against a
primary source, recorded as unknown rather than filled in with the answer
everyone repeats.

The standard ACH per-transaction limit is the useful example. It is widely
repeated as "no limit", but no primary Nacha or FRFS page we have read says so,
and "widely repeated" is not the same as "stated by the operator", so it sits in
`gaps` until somebody finds the sentence.

The Fedwire limit used to sit there too, and it should not have. A Federal
Reserve Board release we were **already citing in the same file** says the
service carries transfers "up to $10 billion". The gap is now the narrower one
it should always have been: the figure is given to the nearest billion in a
descriptive sentence, and Operating Circular 6 sets no per-transaction cap for
normal operation, so the exact maximum and where it is set remain unconfirmed.
That one was caught in review rather than by us, which is the argument for
publishing this at all.

This is the part of the repo we would most like corrections on.

## Staleness

[scripts/check_sources.py](scripts/check_sources.py) re-fetches every source URL,
strips the markup, and hashes the visible text. When a page changes, it says so.
It does not try to work out what changed, because that needs a human who knows
which sentence mattered.

```bash
python3 scripts/check_sources.py            # report changes
python3 scripts/check_sources.py --update   # accept current state as baseline
```

[.github/workflows/check-sources.yml](.github/workflows/check-sources.yml) runs it
monthly and opens an issue only when something actually moved. A quiet month
produces no issue and no email.

Hashing rendered text rather than raw bytes is deliberate. Hash the bytes and
every run reports every page as changed, because of rotating tokens and build
ids, and an alert that always fires is an alert you stop reading.

## Status

This is a maintained reference, not a supported product. Issues and pull
requests get reviewed periodically rather than on a schedule.

Corrections merge fastest when they come with the source URL and the sentence
you are relying on. A correction that fills in something from `gaps` is the most
useful contribution available.

It covers four US rails. It does not cover Canada, the EU, return code
semantics, or fee schedules. Those are real gaps rather than an oversight, and
adding them badly would be worse than leaving them out.

## Licence

Data in `rails/` is [CC BY 4.0](LICENSE-DATA). Code is [Apache 2.0](LICENSE).
Attribution is the point. If a figure from here ends up in your internal wiki or
a vendor comparison, the licence asks that the source comes with it.

## Who maintains it

[AZdev](https://azdv.co/). We get called in when a payments integration is late
or a sponsor bank has started asking questions, which is how we ended up caring
whether these numbers are right.
