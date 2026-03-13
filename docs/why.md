# Why Assistant Exists

Most of the messages that reach you every day were not written for you.

A company updates its terms of service and sends you an email about it. That email exists because a legal team needs a paper trail showing they notified you. It was not written for you. If it were actually written for you, it would tell you what _changed_. Instead you get a 12-page document written in legalese with a subject line like "We've updated our Terms of Service" and zero indication of how the changes impacts you.

This is the default state of digital communication. The cost for a sender to reach you is basically zero. The cost for you to process what they sent is not zero. Your attention is the thing being spent, and you're not the one choosing to spend it.

Remember, spam laws were crafted by lawyers, not humans.

## Sender-benefit vs receiver-benefit

A useful way to think about this: some messages are written for the receiver and some are written purely for the sender.

A personal email from a friend asking how you're doing? That's receiver-benefit. It was written with you in mind. A notification that your password was changed? Also receiver-benefit, assuming you didn't change it yourself.

A marketing email from a SaaS product you used once three years ago? Sender-benefit. A terms of service update with no summary of changes? Sender-benefit. A "Your weekly digest" email you never asked for? Sender-benefit.

The ratio is not close. The vast majority of what lands in your inbox exists to serve the sender. But your email client treats every message the same. They all get a bold subject line and a notification. They all compete for the same scarce resource: your attention.

## What Assistant does about it

Assistant uses AI to sort this out. It reads your email, classifies each message, and takes action based on rules you define. The simplest version of this is: identify messages that are not worth your human attention and archive them. No notification, no bold subject line, no context switch. Just quietly moved out of the way.

That alone is useful. But the more interesting possibility goes further.

Take that terms of service email. Right now you have two options: ignore it or read a 12-page legal document. Assistant could, in theory, fetch the previous version of the terms, diff it against the new one, and surface the actual changes. Turn a message that was sent purely for the sender's benefit into information that actually benefits you.

This is the core idea. Not just filtering out noise, but transforming sender-benefit communication into receiver-benefit communication. Changing the equation so that what reaches you is actually worth your time.

## Why safety matters here

An AI assistant that touches your inbox is useful exactly up to the point where it does something wrong. Delete an important email, send a reply you didn't approve, leak private content to an external service. One bad action and the trust is gone.

That's why Assistant treats safety as a structural property of the system, not a feature you bolt on. Every autonomous action must be reversible. The AI classifies, but deterministic code decides what actions to take. The AI's output is treated as untrusted input, always. If something cannot be undone, a human has to approve it first.

These aren't guidelines. They're enforced in code. See the [safety model](architecture/safety-model.md) for details on how.

## Beyond email

Email is the first integration and the most mature one, but the sender-benefit vs receiver-benefit framing applies broadly. GitHub notifications, Slack messages, calendar invites. Any channel where the volume of incoming information outpaces your ability to process it is a channel where Assistant can help separate what matters from what doesn't.

The goal is not to replace your judgment. It's to stop wasting it on things that don't deserve it.
