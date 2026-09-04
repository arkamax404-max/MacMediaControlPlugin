#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
#import <math.h>

typedef void (^MRNowPlayingCallback)(CFDictionaryRef information);
extern void MRMediaRemoteGetNowPlayingInfo(dispatch_queue_t queue, MRNowPlayingCallback callback);
extern void MRMediaRemoteSendCommand(int command, id options);

static const int NowPlayingTimeoutMilliseconds = 2500;
static const int CommandConfirmationTimeoutMilliseconds = 1500;
static const int CommandPollMilliseconds = 100;
static const int CommandNowPlayingTimeoutMilliseconds = 250;

static void writeJSON(NSDictionary *payload) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:nil];
    if (data != nil) {
        fwrite(data.bytes, 1, data.length, stdout);
        fputc('\n', stdout);
    }
}

static NSNumber *number(NSDictionary *info, NSString *key) {
    id value = info[key];
    return [value isKindOfClass:[NSNumber class]] ? value : @0;
}

static NSString *text(NSDictionary *info, NSString *key) {
    id value = info[key];
    return [value isKindOfClass:[NSString class]] ? value : @"";
}

static NSString *timestamp(NSDictionary *info) {
    id value = info[@"kMRMediaRemoteNowPlayingInfoTimestamp"];
    if ([value isKindOfClass:[NSDate class]]) {
        return [[NSISO8601DateFormatter new] stringFromDate:value];
    }
    return [value isKindOfClass:[NSString class]] ? value : nil;
}

static NSDictionary *nowPlayingInfo(int timeoutMilliseconds) {
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    __block NSDictionary *info = nil;
    MRMediaRemoteGetNowPlayingInfo(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^(CFDictionaryRef information) {
        info = information ? CFBridgingRelease(CFRetain(information)) : @{};
        dispatch_semaphore_signal(completed);
    });
    if (dispatch_semaphore_wait(completed, dispatch_time(DISPATCH_TIME_NOW, (int64_t)timeoutMilliseconds * NSEC_PER_MSEC)) != 0) {
        return nil;
    }
    return info;
}

static void readNowPlaying(void) {
    NSDictionary *info = nowPlayingInfo(NowPlayingTimeoutMilliseconds);
    if (info == nil) {
        exit(124);
    }
    NSData *artwork = [info[@"kMRMediaRemoteNowPlayingInfoArtworkData"] isKindOfClass:[NSData class]]
            ? info[@"kMRMediaRemoteNowPlayingInfoArtworkData"] : nil;
    NSNumber *rate = number(info, @"kMRMediaRemoteNowPlayingInfoPlaybackRate");
    NSMutableDictionary *media = [@{ @"state": rate.doubleValue > 0 ? @"playing" : @"paused",
        @"title": text(info, @"kMRMediaRemoteNowPlayingInfoTitle"),
        @"artist": text(info, @"kMRMediaRemoteNowPlayingInfoArtist"),
        @"duration": number(info, @"kMRMediaRemoteNowPlayingInfoDuration"),
        @"position": number(info, @"kMRMediaRemoteNowPlayingInfoElapsedTime"),
        @"playback_rate": rate,
        @"artwork": artwork ? [artwork base64EncodedStringWithOptions:0] : [NSNull null] } mutableCopy];
    NSString *sourceTimestamp = timestamp(info);
    if (sourceTimestamp != nil) media[@"position_updated_at"] = sourceTimestamp;
    writeJSON(@{ @"status": @"ok", @"media": media });
}

static NSDictionary *commandSnapshot(NSDictionary *info) {
    if (info == nil) return nil;
    NSString *identifier = text(info, @"kMRMediaRemoteNowPlayingInfoUniqueIdentifier");
    NSArray *identity = identifier.length > 0 ? @[identifier] : @[
        text(info, @"kMRMediaRemoteNowPlayingInfoTitle"),
        text(info, @"kMRMediaRemoteNowPlayingInfoArtist"),
        text(info, @"kMRMediaRemoteNowPlayingInfoAlbum"),
        number(info, @"kMRMediaRemoteNowPlayingInfoDuration")];
    return @{ @"playing": @(number(info, @"kMRMediaRemoteNowPlayingInfoPlaybackRate").doubleValue > 0),
              @"identity": identity,
              @"position": number(info, @"kMRMediaRemoteNowPlayingInfoElapsedTime"),
              @"rate": number(info, @"kMRMediaRemoteNowPlayingInfoPlaybackRate"),
              @"sampledAt": @([NSDate timeIntervalSinceReferenceDate]) };
}

static BOOL commandEffectObserved(NSString *operation, NSDictionary *before, NSDictionary *after) {
    if ([operation isEqualToString:@"toggle"]) {
        return ![before[@"playing"] isEqual:after[@"playing"]];
    }
    if (![before[@"identity"] isEqual:after[@"identity"]]) return YES;
    double elapsed = [after[@"sampledAt"] doubleValue] - [before[@"sampledAt"] doubleValue];
    double expected = [before[@"position"] doubleValue] +
        (elapsed * ([before[@"playing"] boolValue] ? [before[@"rate"] doubleValue] : 0.0));
    return fabs([after[@"position"] doubleValue] - expected) >= 2.0;
}

static void sendCommandAndConfirm(NSString *operation, int command) {
    NSDictionary *before = commandSnapshot(nowPlayingInfo(CommandNowPlayingTimeoutMilliseconds));
    if (before == nil) {
        writeJSON(@{ @"status": @"rejected" });
        return;
    }
    MRMediaRemoteSendCommand(command, nil);
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:(CommandConfirmationTimeoutMilliseconds / 1000.0)];
    while ([deadline timeIntervalSinceNow] > 0) {
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:(CommandPollMilliseconds / 1000.0)]];
        NSDictionary *after = commandSnapshot(nowPlayingInfo(CommandNowPlayingTimeoutMilliseconds));
        if (after != nil && commandEffectObserved(operation, before, after)) {
            writeJSON(@{ @"status": @"ok" });
            return;
        }
    }
    writeJSON(@{ @"status": @"rejected" });
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) return 64;
        NSString *operation = [NSString stringWithUTF8String:argv[1]];
        if ([operation isEqualToString:@"read"]) {
            readNowPlaying();
            return 0;
        }
        NSDictionary<NSString *, NSNumber *> *commands = @{ @"toggle": @2, @"next": @4, @"previous": @5 };
        NSNumber *command = commands[operation];
        if (command == nil) return 64;
        sendCommandAndConfirm(operation, command.intValue);
        return 0;
    }
}
