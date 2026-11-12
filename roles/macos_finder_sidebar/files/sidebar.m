#import <CoreServices/CoreServices.h>
#import <Foundation/Foundation.h>
#pragma clang diagnostic ignored "-Wdeprecated-declarations"

static NSArray *lists(void) {
    return @[ @"FavoriteItems", @"FavoriteVolumes", @"TopSidebarSection" ];
}

static LSSharedFileListRef openList(NSString *name) {
    CFStringRef type = (__bridge CFStringRef)[@"com.apple.LSSharedFileList." stringByAppendingString:name];
    return LSSharedFileListCreate(NULL, type, NULL);
}

static NSArray *items(LSSharedFileListRef list) {
    UInt32 seed;
    return CFBridgingRelease(LSSharedFileListCopySnapshot(list, &seed));
}

static NSString *urlOf(id item) {
    CFURLRef url = LSSharedFileListItemCopyResolvedURL((__bridge LSSharedFileListItemRef)item, 0, NULL);
    return url ? [(__bridge_transfer NSURL *)url absoluteString] : @"";
}

static int readAll(void) {
    for (NSString *name in lists()) {
        LSSharedFileListRef list = openList(name);
        if (!list) {
            fprintf(stderr, "cannot open the list %s\n", name.UTF8String);
            return 1;
        }
        for (id item in items(list)) printf("%s\t%s\n", name.UTF8String, urlOf(item).UTF8String);
        CFRelease(list);
    }
    return 0;
}

static int removeItems(NSString *name, NSSet *urls) {
    LSSharedFileListRef list = openList(name);
    if (!list) {
        fprintf(stderr, "cannot open the list %s\n", name.UTF8String);
        return 1;
    }
    int status = 0;
    for (id item in items(list)) {
        NSString *url = urlOf(item);
        if ([urls containsObject:url] && LSSharedFileListItemRemove(list, (__bridge LSSharedFileListItemRef)item) != noErr) {
            fprintf(stderr, "%s refused the removal of %s\n", name.UTF8String, url.UTF8String);
            status = 1;
        }
    }
    CFRelease(list);
    return status;
}

static void removeDuplicates(LSSharedFileListRef list, LSSharedFileListItemRef kept) {
    NSString *url = urlOf((__bridge id)kept);
    UInt32 keptID = LSSharedFileListItemGetID(kept);
    for (id item in items(list)) {
        LSSharedFileListItemRef other = (__bridge LSSharedFileListItemRef)item;
        if (LSSharedFileListItemGetID(other) != keptID && [urlOf(item) isEqualToString:url])
            LSSharedFileListItemRemove(list, other);
    }
}

static int favoritesFirst(NSArray *paths) {
    LSSharedFileListRef list = openList(@"FavoriteItems");
    if (!list) {
        fprintf(stderr, "cannot open the list FavoriteItems\n");
        return 1;
    }
    LSSharedFileListItemRef after = kLSSharedFileListItemBeforeFirst;
    int status = 0;
    for (NSString *path in paths) {
        if ([@[ @"Desktop", @"Documents", @"Downloads" ] containsObject:path.lastPathComponent])
            [[NSFileManager defaultManager] contentsOfDirectoryAtPath:path error:NULL];
        NSURL *url = [NSURL fileURLWithPath:path isDirectory:YES];
        LSSharedFileListItemRef added = LSSharedFileListInsertItemURL(list, after, NULL, NULL, (__bridge CFURLRef)url, NULL, NULL);
        if (!added) {
            fprintf(stderr, "refused %s\n", path.UTF8String);
            status = 3;
            break;
        }
        removeDuplicates(list, added);
        if (after != kLSSharedFileListItemBeforeFirst) CFRelease(after);
        after = added;
    }
    if (after != kLSSharedFileListItemBeforeFirst) CFRelease(after);
    CFRelease(list);
    return status;
}

int main(void) {
    @autoreleasepool {
        NSArray *args = [[NSProcessInfo processInfo] arguments];
        NSString *command = args.count > 1 ? args[1] : @"";
        if ([command isEqualToString:@"read"] && args.count == 2) return readAll();
        if ([command isEqualToString:@"remove"] && args.count > 3 && [lists() containsObject:args[2]])
            return removeItems(args[2], [NSSet setWithArray:[args subarrayWithRange:NSMakeRange(3, args.count - 3)]]);
        if ([command isEqualToString:@"first"] && args.count > 2)
            return favoritesFirst([args subarrayWithRange:NSMakeRange(2, args.count - 2)]);
        fprintf(stderr, "usage: sidebar read | remove LIST URL... | first PATH...\n");
        return 2;
    }
}
