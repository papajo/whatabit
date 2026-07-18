# WhataBit 0.2 Work Breakdown Structure

## 1. Product and Project Management

1.1 Define 0.2 product goal and non-goals.  
1.2 Maintain backlog and priority order.  
1.3 Maintain sprint plan and demo/retro notes.  
1.4 Maintain risk/decision logs.  
1.5 Keep README and OKF synchronized with actual behavior.

## 2. Web UI

2.1 Local server startup and routing.  
2.2 Torrent upload and validation.  
2.3 Uploaded torrent library and deletion.  
2.4 Metadata display.  
2.5 Download settings form.  
2.6 Start/stop controls.  
2.7 Live progress/status display.  
2.8 Completed output download/open link.  
2.9 Error and empty-state UX.  
2.10 Responsive layout checks.

## 3. Download Engine

3.1 Tracker announce orchestration.  
3.2 Peer connection lifecycle.  
3.3 Piece and block scheduling.  
3.4 In-flight request tracking.  
3.5 Request timeout and retry.  
3.6 Piece hash verification and requeue.  
3.7 Peer scoring/banning basics.  
3.8 Safe stop/cancel behavior.  
3.9 Output assembly and write safety.  
3.10 Multi-file behavior definition.

## 4. Persistence

4.1 Uploaded torrent storage.  
4.2 Session/job metadata store.  
4.3 Output path and status persistence.  
4.4 Partial progress strategy.  
4.5 Recheck/resume behavior.  
4.6 Cleanup/remove flows.

## 5. Quality and Testing

5.1 Bencode tests.  
5.2 Torrent parser tests.  
5.3 Tracker response parser tests.  
5.4 Peer handshake/message tests.  
5.5 Piece/block scheduling tests.  
5.6 Web UI API smoke tests.  
5.7 Legal real-torrent smoke test checklist.  
5.8 Git hygiene checks.

## 6. Documentation

6.1 README usage and safety docs.  
6.2 Project planning docs.  
6.3 Known limitations.  
6.4 Troubleshooting guide.  
6.5 Release notes for 0.2.
