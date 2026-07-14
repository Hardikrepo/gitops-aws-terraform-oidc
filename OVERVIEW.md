# GitOps AWS OIDC — A Plain-English Guide

This page explains the project without assuming any coding or cloud
background. If you want the full technical reference (exact commands, file
layout, IAM policies), see [`README.md`](README.md) instead — this page is
the "what is this and why should I care" version.

## What is this, in one sentence?

A system that lets a team safely change their Amazon cloud setup by
proposing the change in writing, having it automatically checked, and only
letting it go live after a person approves it — without ever handing out
permanent passwords to make those changes.

## The problem this solves

Normally, when an automated system needs to make changes to a company's
cloud account, it's given a username and password (or a "key") that works
forever until someone remembers to cancel it. That's risky — it's like
handing out a master key to the building that never expires. If that key
ever leaks (gets pasted somewhere it shouldn't, stolen, or forgotten about),
whoever has it can let themselves in indefinitely.

This project does it differently: instead of a permanent key, the automated
system requests a **temporary visitor badge** every single time it needs to
do something. The badge is issued fresh, only works for a few minutes, only
opens the one door it's allowed through, and is void the moment the task
finishes. There is nothing sitting around that could leak, because nothing
long-lived exists in the first place.

## What actually happens, step by step

1. **Someone proposes a change.** A developer writes what they want changed
   about the cloud setup and submits it for review — like submitting a
   document with "track changes" turned on, so exactly what's different is
   visible to everyone.
2. **Robots check it automatically.** Within a couple of minutes, an
   automated system tries the change out safely (without actually applying
   it yet) and reports back whether it would work.
3. **An AI assistant reviews it for risk.** Before any human even looks,
   Claude (an AI) reads the proposed change and writes a short note flagging
   anything concerning — for example, "this would permanently delete a
   database" or "this widens who has access to something sensitive."
4. **It's tried in safe environments first.** The change is applied to a
   practice copy of the setup (called `dev`), then a rehearsal copy
   (`staging`), before it's ever allowed near the real, live system.
5. **A person approves the real thing.** Only a designated approver can let
   the change reach `prod` — the actual live environment customers or the
   business depend on. Nothing reaches production silently.
6. **If something breaks, a second AI assistant notices.** A separate
   watchdog checks every 30 minutes whether a change got stuck partway
   through (for example, if the automation crashed mid-task). If it finds
   one, it writes up what it found and waits for a person to decide what to
   do — it never fixes things on its own.

## The pieces, explained without jargon

| Term you'll see | What it actually means |
|---|---|
| **AWS** | Amazon's cloud — the actual servers and storage the company's systems run on |
| **GitHub** | Where the proposed changes are written, discussed, and history-tracked — plus where the "robots" (automated checks) live and run |
| **Terraform** | The tool that describes the cloud setup as plain text files, so every change is visible and reversible, like editing a recipe instead of pulling levers by hand |
| **OIDC** | The mechanism that hands out those temporary visitor badges instead of permanent passwords |
| **`dev` / `staging` / `prod`** | Practice copy → rehearsal copy → the real thing. Changes always flow in that order, never straight to `prod` |
| **Claude / AI agents** | Two automated assistants: one reviews proposed changes for risk before they're approved, the other watches for things that got stuck and need a human's attention |

## What's already built and running

This particular project is not a demo sitting on someone's laptop — it's
live: the cloud setup exists in a real AWS account, the repository is public
on GitHub, and the automated checks and AI assistants are active right now.

## How to actually get started

There are two very different things "getting started" can mean here —
pick the one that matches what you're trying to do.

### A. I want to propose or approve a change (no coding needed)

1. Go to the project on GitHub: `github.com/Hardikrepo/gitops-aws-terraform-oidc`.
2. To see what's currently proposed, open the **Pull Requests** tab —
   each one is a proposed change, with the automated check results and the
   AI's risk review visible as comments underneath it.
3. If you're a designated approver for production, you'll get a
   notification asking you to approve before a change reaches `prod` — this
   happens in GitHub's **Actions** tab, under the run that's waiting.
4. You don't need to install anything or run any commands to do either of
   these — it all happens in the browser.

### B. I want to set this up for my own AWS account from scratch

This part genuinely needs someone comfortable with a command line — it
involves running Terraform once and configuring a few things in GitHub. The
plain-language version of what has to happen, in order:

1. **Someone with AWS access runs the one-time setup.** This creates the
   trust relationship between GitHub and AWS, plus the storage for tracking
   changes. It's done once, by hand, and never again unless the setup
   itself changes.
2. **The project's code is pushed to a GitHub repository.**
3. **A few settings are configured in GitHub** — mainly, who's allowed to
   approve production changes, and a handful of values produced by step 1.
4. **From then on, everything in section A above just works** — anyone can
   propose changes through GitHub, and the automation handles the rest.

For the exact commands, file structure, and configuration values used in
step 1–3, see [`README.md`](README.md) — that's the reference a developer
would follow to actually execute this.

## Why this approach, instead of the simpler/older way?

The older, more common way is to generate a permanent AWS password once and
paste it into GitHub as a saved secret. It's faster to set up, but it means
a long-lived credential sits there indefinitely — if it ever leaks, whoever
has it can access the cloud account until someone notices and manually
revokes it. This project trades a bit of one-time setup complexity for
removing that risk entirely: there is no long-lived password to leak,
because none is ever created.
